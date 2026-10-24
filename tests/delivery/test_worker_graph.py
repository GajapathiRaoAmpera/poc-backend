from datetime import timedelta

import pytest

from app.core.documents import now
from app.llm.base import ProviderError
from app.llm.mock import MockProvider
from app.modules.agents import base as agents
from app.modules.artifacts import service as artifacts
from app.modules.delivery import service as delivery
from app.modules.delivery.threads import get_thread
from app.worker.runner import Worker
from tests.agents.helpers import good_requirements, setup_project
from tests.delivery.conftest import scripted

NO_QUESTIONS = scripted(requirements_output=good_requirements(open_questions=[]))


class Crash(BaseException):
    """Simulates the worker process dying mid-node."""


def worker(db, script=NO_QUESTIONS, **kw):
    provider = MockProvider(script=script)
    w = Worker(
        db, provider_factory=lambda: provider, worker_id="w1", heartbeat_s=0.05, poll_s=0.01, **kw
    )
    w.provider = provider
    return w


async def _started(db, script=NO_QUESTIONS):
    owner, project = await setup_project(db)
    await delivery.start_delivery(db, project, owner)
    return owner, project, worker(db, script)


async def test_graph_job_runs_to_first_gate(db):
    _, project, w = await _started(db)
    assert (await get_thread(db, project)).status == "RUNNING"  # queued
    assert await w.run_once() is True
    t = await get_thread(db, project)
    assert t.status == "PAUSED" and t.pending["kind"] == "approval"
    assert t.pending["stage"] == "requirements" and t.plan_version == 1
    job = await db.jobs.find_one({"type": "graph_step"})
    assert job["status"] == "DONE" and job["idempotency_key"] is None
    graph_events = [
        e async for e in db.run_events.find({"project_id": project.id, "type": "graph"})
    ]
    assert graph_events and graph_events[-1]["data"]["status"] == "PAUSED"


async def test_crash_after_node_no_duplicate(db, monkeypatch):
    _, project, w = await _started(db)
    real = agents.get_agent("requirements_analyst")
    crashed = {"done": False}

    async def crash_once(ctx):
        outcome = await real(ctx)
        if not crashed["done"]:
            crashed["done"] = True
            raise Crash()
        return outcome

    monkeypatch.setitem(agents._REGISTRY, "requirements_analyst", crash_once)
    with pytest.raises(Crash):
        await w.run_once()
    await db.jobs.update_one({}, {"$set": {"lease_expires_at": now() - timedelta(seconds=1)}})
    assert await worker(db).run_once() is True
    revs = await db.artifact_revisions.count_documents({"project_id": project.id})
    req_runs = [r async for r in db.agent_runs.find({"stage": "requirements"})]
    assert revs == 1
    assert len(req_runs) == 1 and req_runs[0]["status"] == "SUCCEEDED"
    assert len(req_runs[0]["attempts"]) == 2
    assert (await get_thread(db, project)).pending["kind"] == "approval"


async def test_retryable_provider_error_retries_job(db):
    calls = {"n": 0}

    def flaky(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("RATE_LIMITED", "slow down", retryable=True)
        return good_requirements(open_questions=[])

    _, project, w = await _started(db, scripted(requirements_output=flaky))
    await w.run_once()
    job = await db.jobs.find_one({"type": "graph_step"})
    assert job["status"] == "QUEUED" and job["last_error"] == "RATE_LIMITED"
    assert job["run_after"] > now()
    assert (await get_thread(db, project)).status == "RUNNING"
    await db.jobs.update_one({}, {"$set": {"run_after": now()}})
    await w.run_once()
    assert (await get_thread(db, project)).pending["kind"] == "approval"
    assert await db.agent_runs.count_documents({"stage": "requirements"}) == 1


async def test_exhausted_retries_thread_failed_and_resume_restarts(db):
    always = scripted(
        requirements_output=lambda s, u: (_ for _ in ()).throw(
            ProviderError("UNAVAILABLE", "down", retryable=True)
        )
    )
    owner, project, w = await _started(db, always)
    for _ in range(3):
        await db.jobs.update_one({}, {"$set": {"run_after": now()}})
        await w.run_once()
    t = await get_thread(db, project)
    assert t.status == "FAILED" and t.pending["kind"] == "failed"
    run = await db.agent_runs.find_one({"stage": "requirements"})
    assert run["status"] == "FAILED" and run["active"] is False
    await delivery.resume(db, project, owner)
    await worker(db).run_once()
    assert (await get_thread(db, project)).pending["kind"] == "approval"


async def test_cancel_running_graph(db, monkeypatch):
    import asyncio

    owner, project, w = await _started(db)

    async def slow(ctx):
        await asyncio.sleep(5)

    monkeypatch.setitem(agents._REGISTRY, "requirements_analyst", slow)
    task = asyncio.create_task(w.run_once())
    for _ in range(100):
        await asyncio.sleep(0.02)
        if await db.agent_runs.count_documents({"stage": "requirements", "status": "RUNNING"}):
            break
    await delivery.cancel(db, project, owner)
    await asyncio.wait_for(task, 3)
    t = await get_thread(db, project)
    assert t.status == "PAUSED" and t.pending["kind"] == "cancelled"
    run = await db.agent_runs.find_one({"stage": "requirements"})
    assert run["status"] == "CANCELLED"


async def test_cancel_queued_graph(db):
    owner, project, _ = await _started(db)
    await delivery.cancel(db, project, owner)
    t = await get_thread(db, project)
    assert t.status == "PAUSED" and t.pending["kind"] == "cancelled"
    assert (await db.jobs.find_one({"type": "graph_step"}))["status"] == "DONE"


async def test_notify_while_running_rechecks_after_pause(db, monkeypatch):
    owner, project, w = await _started(db)
    real = agents.get_agent("requirements_analyst")

    async def approve_during_run(ctx):
        outcome = await real(ctx)
        # Simulates an approval landing while the graph is still running.
        await delivery.notify(db, project, "answers")
        return outcome

    monkeypatch.setitem(agents._REGISTRY, "requirements_analyst", approve_during_run)
    await w.run_once()
    assert (await get_thread(db, project)).status == "RUNNING"  # recheck enqueued
    job = await db.jobs.find_one({"status": "QUEUED", "type": "graph_step"})
    assert job["input"]["trigger"] == "answers"


async def test_approval_resume_flow(db):
    owner, project, w = await _started(db)
    await w.run_once()
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    assert await delivery.notify(db, project, "approved:requirements") is True
    await w.run_once()
    t = await get_thread(db, project)
    assert t.pending["stage"] == "architecture" and t.pending["kind"] == "approval"


async def test_agent_run_jobs_unaffected(db):
    from app.modules.orchestration.service import start_stage

    owner, project = await setup_project(db)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    await worker(db).run_once()
    assert (await db.agent_runs.find_one({"_id": run.id}))["status"] == "SUCCEEDED"
