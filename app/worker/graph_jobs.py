"""Executing ``graph_step`` jobs: drive a project's delivery graph until it pauses or ends.

How the graph is invoked is decided from its checkpointed state on every attempt, so retries and
crash recovery are correct: a pending interrupt is resumed with the user's action, an interrupted
step continues from the last checkpoint, and an ended (or new) graph starts again.
"""

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from langgraph.types import Command

from app.core.documents import now
from app.llm.base import ProviderError
from app.modules.delivery import service as delivery
from app.modules.delivery.state import GraphDeps
from app.modules.delivery.threads import set_thread
from app.modules.orchestration import runs
from app.modules.orchestration.models import AgentRun, Job, RunError, RunStatus
from app.modules.projects.models import Project

if TYPE_CHECKING:
    from app.worker.runner import Worker

logger = logging.getLogger(__name__)


async def _graph_input(graph, cfg: dict, project: Project, inp: dict[str, Any]):
    state = await graph.aget_state(cfg)
    if any(t.interrupts for t in state.tasks):
        return Command(resume={"trigger": inp.get("trigger"), "message": inp.get("message")})
    if state.next:
        return None  # interrupted mid-step: continue from the last checkpoint
    return {"trigger": inp.get("trigger"), "project_id": str(project.id)}


async def _finish(w: "Worker", job: Job, **fields: Any) -> None:
    await w.db.jobs.update_one(
        {"_id": job.id, "lease_owner": w.worker_id},
        {
            "$set": {
                "status": "DONE",
                "lease_owner": None,
                "lease_expires_at": None,
                "idempotency_key": None,
                **fields,
            }
        },
    )


async def _abandon_running_runs(
    w: "Worker", project: Project, status: RunStatus, code: str, message: str
) -> None:
    """Graph runs left RUNNING by a failed/cancelled graph step must not stay active."""
    async for doc in w.db.agent_runs.find(
        {"project_id": project.id, "status": "RUNNING", "graph_key": {"$type": "string"}}
    ):
        run = AgentRun.from_doc(doc)
        extra = (
            {}
            if status == RunStatus.CANCELLED
            else {"error": RunError(code=code, message=message).model_dump()}
        )
        with contextlib.suppress(runs.IllegalTransition):
            await runs.transition(w.db, run.id, {RunStatus.RUNNING}, status, **extra)


async def _heartbeat(w: "Worker", job: Job, project: Project, task: asyncio.Future, flag: dict):
    while True:
        await asyncio.sleep(w.heartbeat_s)
        await w.db.jobs.update_one(
            {"_id": job.id, "lease_owner": w.worker_id},
            {"$set": {"lease_expires_at": now() + timedelta(seconds=w.lease_s)}},
        )
        doc = await w.db.delivery_threads.find_one(
            {"project_id": project.id}, {"cancel_requested": 1}
        )
        if doc and doc.get("cancel_requested"):
            flag["set"] = True
            task.cancel()
            return


async def _paused(w: "Worker", project: Project, cfg: dict) -> None:
    state = await w.graph.aget_state(cfg)
    pending = next((t.interrupts[0].value for t in state.tasks if t.interrupts), None)
    fields = {"plan_version": state.values.get("plan_version"), "last_error": None}
    if pending is not None:
        await set_thread(w.db, project.id, status="PAUSED", pending=pending, **fields)
    else:
        await set_thread(w.db, project.id, status="DONE", pending=None, **fields)
    # Someone acted while we were running: look again with fresh facts.
    was = await w.db.delivery_threads.find_one_and_update(
        {"project_id": project.id, "dirty": {"$type": "string"}}, {"$set": {"dirty": None}}
    )
    if was is not None:
        await delivery.notify(w.db, project, was["dirty"])


async def run_graph_job(w: "Worker", job: Job) -> None:
    project = Project.from_doc(await w.db.projects.find_one({"_id": job.project_id}))
    thread = await w.db.delivery_threads.find_one({"project_id": project.id})
    if thread and thread.get("cancel_requested"):
        await _finish(w, job)
        await set_thread(
            w.db, project.id, status="PAUSED", pending={"kind": "cancelled"}, cancel_requested=False
        )
        return
    await set_thread(w.db, project.id, status="RUNNING", pending=None)
    cfg = {"configurable": {"thread_id": str(project.id)}}
    flag = {"set": False}
    deps = GraphDeps(db=w.db, provider_factory=w.provider_factory, cancelled=lambda: flag["set"])
    inp = delivery.job_input(job)
    graph_input = await _graph_input(w.graph, cfg, project, inp)
    task = asyncio.ensure_future(w.graph.ainvoke(graph_input, cfg, context=deps))
    heartbeat = asyncio.create_task(_heartbeat(w, job, project, task, flag))
    try:
        await asyncio.wait_for(asyncio.shield(task), job.timeout_s)
    except asyncio.CancelledError:
        if not flag["set"]:
            raise
        await _abandon_running_runs(w, project, RunStatus.CANCELLED, "CANCELLED", "Cancelled")
        await _finish(w, job)
        await set_thread(
            w.db, project.id, status="PAUSED", pending={"kind": "cancelled"}, cancel_requested=False
        )
    except (ProviderError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError):
            task.cancel()
            code, message, retryable = "TIMEOUT", "The workflow step took too long", True
        else:
            code, message, retryable = exc.code, exc.message, exc.retryable
        await _retry_or_fail(w, job, project, code, message, retryable)
    except Exception:
        logger.exception("Delivery graph failed for project %s", project.id)
        await _fail(w, job, project, "INTERNAL", "The delivery workflow failed unexpectedly")
    else:
        await _finish(w, job)
        await _paused(w, project, cfg)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat


async def _fail(w: "Worker", job: Job, project: Project, code: str, message: str) -> None:
    await _abandon_running_runs(w, project, RunStatus.FAILED, code, message)
    await _finish(w, job, last_error=code)
    await set_thread(
        w.db,
        project.id,
        status="FAILED",
        last_error=code,
        pending={
            "kind": "failed",
            "stage": None,
            "payload": {"reason": message},
            "reason": message,
        },
    )


async def _retry_or_fail(w, job, project, code, message, retryable) -> None:
    from app.worker.runner import retry_delay

    if not retryable or job.attempts >= job.max_attempts:
        await _fail(w, job, project, code, message)
        return
    delay = retry_delay(code, job.attempts)
    await w.db.jobs.update_one(
        {"_id": job.id, "lease_owner": w.worker_id},
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
    await set_thread(w.db, project.id, last_error=code)
