"""Durable worker: claims jobs from MongoDB with a lease, runs the agent, records the outcome.

Crash safety: a job whose lease expires is reclaimed by any worker; agent writes are idempotent
per run, so re-running after a crash never duplicates output.
"""

import asyncio
import contextlib
import logging
import os
import random
import socket
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from pymongo import ReturnDocument

from app.core.db import Db
from app.core.documents import now
from app.llm.base import LLMProvider, ProviderError
from app.modules.agents.base import (
    AgentContext,
    AgentOutcome,
    OutputInvalid,
    get_agent,
    run_finished_hooks,
)
from app.modules.orchestration import events, runs
from app.modules.orchestration.models import AgentRun, Job, RunError, RunStatus
from app.modules.projects.models import Project
from app.worker import graph_jobs

logger = logging.getLogger(__name__)

BACKOFF_BASE_S = 5


class _CancelledByUser(Exception):
    pass


def backoff_seconds(attempt: int) -> float:
    return BACKOFF_BASE_S * 2**attempt * random.uniform(0.8, 1.2)  # noqa: S311 (jitter, not crypto)


# Provider rate limits are per minute: retrying sooner only spends another attempt.
RATE_LIMIT_WAIT_S = 60


def retry_delay(code: str | None, attempt: int) -> float:
    delay = backoff_seconds(attempt)
    if code != "RATE_LIMITED":
        return delay
    return max(delay, RATE_LIMIT_WAIT_S * random.uniform(1.0, 1.2))  # noqa: S311 (jitter)


class Worker:
    def __init__(
        self,
        db: Db,
        provider_factory: Callable[[], LLMProvider],
        worker_id: str | None = None,
        lease_s: float = 60,
        heartbeat_s: float = 20,
        poll_s: float = 1.0,
        graph=None,
    ) -> None:
        self.db = db
        self._graph = graph
        self.provider_factory = provider_factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.lease_s = lease_s
        self.heartbeat_s = heartbeat_s
        self.poll_s = poll_s

    async def _claim(self) -> Job | None:
        t = now()
        doc = await self.db.jobs.find_one_and_update(
            {
                "$or": [
                    {"status": "QUEUED", "run_after": {"$lte": t}},
                    {"status": "RUNNING", "lease_expires_at": {"$lt": t}},
                ]
            },
            {
                "$set": {
                    "status": "RUNNING",
                    "lease_owner": self.worker_id,
                    "lease_expires_at": t + timedelta(seconds=self.lease_s),
                },
                "$inc": {"attempts": 1},
            },
            sort=[("run_after", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return Job.from_doc(doc) if doc else None

    async def _finish_job(self, job: Job, **fields: Any) -> None:
        await self.db.jobs.update_one(
            {"_id": job.id, "lease_owner": self.worker_id},
            {"$set": {"status": "DONE", "lease_owner": None, "lease_expires_at": None, **fields}},
        )

    async def _record_attempt(
        self, run: AgentRun, job: Job, outcome: str, error_code=None, usage: dict | None = None
    ) -> None:
        usage = usage or {}
        await self.db.agent_runs.update_one(
            {"_id": run.id, "attempts.n": job.attempts},
            {
                "$set": {
                    "attempts.$.finished_at": now(),
                    "attempts.$.outcome": outcome,
                    "attempts.$.error_code": error_code,
                    "attempts.$.input_tokens": usage.get("input_tokens"),
                    "attempts.$.output_tokens": usage.get("output_tokens"),
                }
            },
        )

    @property
    def graph(self):
        if self._graph is None:
            from app.modules.delivery.checkpoint import make_checkpointer
            from app.modules.delivery.graph import build_graph

            self._graph = build_graph(make_checkpointer(self.db))
        return self._graph

    async def run_once(self) -> bool:
        job = await self._claim()
        if job is None:
            return False
        if job.type == "graph_step":
            await graph_jobs.run_graph_job(self, job)
            return True
        doc = await self.db.agent_runs.find_one({"_id": job.run_id})
        run = AgentRun.from_doc(doc)
        project = Project.from_doc(await self.db.projects.find_one({"_id": run.project_id}))

        if run.graph_key:
            logger.error(
                "Graph run %s was queued as a standalone agent job; completing orphaned job",
                run.id,
            )
            await self._finish_job(job, last_error="GRAPH_RUN_MISCLASSIFIED")
            return True

        if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
            await self._finish_job(job)  # e.g. cancelled while queued
            return True
        if run.cancel_requested:
            await runs.transition(self.db, run.id, {run.status}, RunStatus.CANCELLED)
            await self._finish_job(job)
            await events.emit(self.db, project.id, run.id, "status", "Cancelled")
            await self._run_finished(run.id)
            return True

        provider = self.provider_factory()
        fields: dict[str, Any] = {
            "provider": provider.name,
            "model": provider.model,
            "is_mock": provider.is_mock,
        }
        if run.started_at is None:
            fields["started_at"] = now()
        if run.status == RunStatus.QUEUED:
            run = await runs.transition(
                self.db, run.id, {RunStatus.QUEUED}, RunStatus.RUNNING, **fields
            )
        else:  # reclaimed after a crashed worker's lease expired
            await self.db.agent_runs.update_one({"_id": run.id}, {"$set": fields})
        await self.db.agent_runs.update_one(
            {"_id": run.id},
            {
                "$push": {
                    "attempts": {
                        "n": job.attempts,
                        "started_at": now(),
                        "finished_at": None,
                        "outcome": None,
                        "error_code": None,
                        "input_tokens": None,
                        "output_tokens": None,
                    }
                }
            },
        )
        await events.emit(
            self.db,
            project.id,
            run.id,
            "status",
            f"Started attempt {job.attempts} of {job.max_attempts}",
        )

        cancel_flag = {"set": False}

        async def emit(type_, message, data=None):
            await events.emit(self.db, project.id, run.id, type_, message, data)

        ctx = AgentContext(
            db=self.db,
            project=project,
            run=run,
            provider=provider,
            emit=emit,
            cancelled=lambda: cancel_flag["set"],
        )
        try:
            agent_fn = get_agent(run.agent)
        except KeyError:
            logger.error("No agent %r registered for run %s", run.agent, run.id)
            await self._fail(job, run, project, "INTERNAL", "This agent can't run on its own")
            return True
        agent_task = asyncio.ensure_future(agent_fn(ctx))
        heartbeat = asyncio.create_task(self._heartbeat(job, run, agent_task, cancel_flag))
        try:
            outcome: AgentOutcome = await asyncio.wait_for(
                asyncio.shield(agent_task), job.timeout_s
            )
        except TimeoutError:
            agent_task.cancel()
            await self._retry_or_fail(
                job, run, project, "TIMEOUT", "The agent took too long to respond", retryable=True
            )
        except asyncio.CancelledError:
            if not cancel_flag["set"]:
                raise
            await self._record_attempt(run, job, "CANCELLED")
            await runs.transition(self.db, run.id, {RunStatus.RUNNING}, RunStatus.CANCELLED)
            await self._finish_job(job)
            await emit("status", "Cancelled")
        except ProviderError as exc:
            await self._retry_or_fail(job, run, project, exc.code, exc.message, exc.retryable)
        except OutputInvalid as exc:
            await self._fail(
                job,
                run,
                project,
                "OUTPUT_INVALID",
                f"The model's output was still invalid after repairs: {exc}",
                data={"issues": exc.issues[:10]},
            )
        except Exception:
            logger.exception("Agent %s failed on run %s", run.agent, run.id)
            await self._fail(job, run, project, "INTERNAL", "The agent failed unexpectedly")
        else:
            await self._record_attempt(run, job, "SUCCEEDED", usage=outcome.usage)
            await runs.transition(
                self.db,
                run.id,
                {RunStatus.RUNNING},
                RunStatus.SUCCEEDED,
                output=outcome.output,
                summary=outcome.summary[:1000],
                usage={
                    "input_tokens": int(outcome.usage.get("input_tokens") or 0),
                    "output_tokens": int(outcome.usage.get("output_tokens") or 0),
                },
            )
            await self._finish_job(job)
            await emit("status", "Succeeded")
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        await self._run_finished(run.id)
        return True

    async def _run_finished(self, run_id: uuid.UUID) -> None:
        doc = await self.db.agent_runs.find_one({"_id": run_id})
        run = AgentRun.from_doc(doc)
        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            return  # retrying
        for hook in run_finished_hooks():
            try:
                await hook(self.db, run)
            except Exception:
                logger.exception("Run-finished hook failed for run %s", run.id)

    async def _heartbeat(
        self, job: Job, run: AgentRun, agent_task: asyncio.Future, cancel_flag: dict
    ) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            await self.db.jobs.update_one(
                {"_id": job.id, "lease_owner": self.worker_id},
                {"$set": {"lease_expires_at": now() + timedelta(seconds=self.lease_s)}},
            )
            doc = await self.db.agent_runs.find_one({"_id": run.id}, {"cancel_requested": 1})
            if doc and doc.get("cancel_requested"):
                cancel_flag["set"] = True
                agent_task.cancel()
                return

    async def _fail(self, job, run, project, code, message, data=None) -> None:
        await self._record_attempt(run, job, "FAILED", error_code=code)
        await runs.transition(
            self.db,
            run.id,
            {RunStatus.RUNNING},
            RunStatus.FAILED,
            error=RunError(code=code, message=message).model_dump(),
        )
        await self._finish_job(job, last_error=code)
        await events.emit(self.db, project.id, run.id, "error", f"Failed: {message}", data)

    async def _retry_or_fail(self, job, run, project, code, message, retryable) -> None:
        if not retryable or job.attempts >= job.max_attempts:
            await self._fail(job, run, project, code, message)
            return
        delay = retry_delay(code, job.attempts)
        await self._record_attempt(run, job, "RETRYING", error_code=code)
        await runs.transition(self.db, run.id, {RunStatus.RUNNING}, RunStatus.QUEUED)
        await self.db.jobs.update_one(
            {"_id": job.id, "lease_owner": self.worker_id},
            {
                "$set": {
                    "status": "QUEUED",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "run_after": now() + timedelta(seconds=delay),
                    "last_error": code,
                }
            },
        )
        await events.emit(
            self.db,
            project.id,
            run.id,
            "status",
            f"Attempt {job.attempts} failed ({message}); retrying in {delay:.0f}s",
        )

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info("Worker %s started", self.worker_id)
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:
                logger.exception("Worker loop error")
                worked = False
            if not worked:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.poll_s)
        logger.info("Worker %s stopped", self.worker_id)
