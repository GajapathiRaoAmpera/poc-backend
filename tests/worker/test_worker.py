import asyncio
from datetime import timedelta

import pytest

from app.core.documents import now
from app.llm.base import ProviderError
from app.llm.mock import MockProvider
from app.modules.agents import base as agents
from app.modules.artifacts import service as artifacts
from app.modules.orchestration.service import cancel_run, start_stage
from app.worker.runner import Worker
from tests.worker.conftest import add_spec


class Crash(BaseException):
    """Simulates the worker process dying (not caught by the worker)."""


@pytest.fixture
def register(monkeypatch):
    def _register(fn):
        monkeypatch.setitem(agents._REGISTRY, "requirements_analyst", fn)

    return _register


def make_worker(db, **kw) -> Worker:
    return Worker(
        db,
        provider_factory=lambda: MockProvider(),
        worker_id="w1",
        lease_s=kw.pop("lease_s", 60),
        heartbeat_s=kw.pop("heartbeat_s", 0.05),
        poll_s=0.01,
        **kw,
    )


async def _run(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    return run


async def _get(db, run):
    return await db.agent_runs.find_one({"_id": run.id})


async def test_success(db, project, owner, register):
    async def agent(ctx):
        await ctx.emit("step", "Doing work")
        return agents.AgentOutcome(
            output={"artifact_id": None, "revision": 1},
            summary="Did it",
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    register(agent)
    run = await _run(db, project, owner)
    assert await make_worker(db).run_once() is True
    doc = await _get(db, run)
    assert doc["status"] == "SUCCEEDED" and doc["summary"] == "Did it"
    assert doc["provider"] == "mock" and doc["is_mock"] is True
    assert doc["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert doc["attempts"][0]["outcome"] == "SUCCEEDED"
    assert (await db.jobs.find_one({"run_id": run.id}))["status"] == "DONE"
    messages = [e["message"] async for e in db.run_events.find({"run_id": run.id}).sort("seq", 1)]
    assert "Doing work" in messages and any("Succeeded" in m for m in messages)


async def test_idle_returns_false(db):
    assert await make_worker(db).run_once() is False


async def test_retryable_error_requeues_with_backoff(db, project, owner, register):
    async def agent(ctx):
        raise ProviderError("RATE_LIMITED", "slow down", retryable=True)

    register(agent)
    run = await _run(db, project, owner)
    before = now()
    await make_worker(db).run_once()
    doc, job = await _get(db, run), await db.jobs.find_one({"run_id": run.id})
    assert doc["status"] == "QUEUED" and job["status"] == "QUEUED" and job["attempts"] == 1
    delay = (job["run_after"] - before).total_seconds()
    assert 10 * 0.8 - 0.5 <= delay <= 10 * 1.2 + 0.5


async def test_gives_up_after_max_attempts(db, project, owner, register):
    async def agent(ctx):
        raise ProviderError("PROVIDER_ERROR", "boom", retryable=True)

    register(agent)
    run = await _run(db, project, owner)
    w = make_worker(db)
    for _ in range(3):
        await db.jobs.update_one({"run_id": run.id}, {"$set": {"run_after": now()}})
        await w.run_once()
    doc = await _get(db, run)
    assert doc["status"] == "FAILED" and doc["error"]["code"] == "PROVIDER_ERROR"
    assert len(doc["attempts"]) == 3
    assert (await db.jobs.find_one({"run_id": run.id}))["status"] == "DONE"


async def test_non_retryable_fails_immediately(db, project, owner, register):
    async def agent(ctx):
        raise agents.OutputInvalid([{"code": "X", "message": "bad edge"}])

    register(agent)
    run = await _run(db, project, owner)
    await make_worker(db).run_once()
    doc = await _get(db, run)
    assert doc["status"] == "FAILED" and doc["error"]["code"] == "OUTPUT_INVALID"
    assert len(doc["attempts"]) == 1


async def test_timeout_is_retryable(db, project, owner, register):
    async def agent(ctx):
        await asyncio.sleep(5)

    register(agent)
    run = await _run(db, project, owner)
    await db.jobs.update_one({"run_id": run.id}, {"$set": {"timeout_s": 0.2}})
    await make_worker(db).run_once()
    doc = await _get(db, run)
    assert doc["status"] == "QUEUED" and doc["attempts"][0]["error_code"] == "TIMEOUT"


async def test_expired_lease_is_reclaimed(db, project, owner, register):
    async def agent(ctx):
        return agents.AgentOutcome(output={}, summary="ok", usage={})

    register(agent)
    run = await _run(db, project, owner)
    await db.jobs.update_one(
        {"run_id": run.id},
        {
            "$set": {
                "status": "RUNNING",
                "lease_owner": "dead",
                "lease_expires_at": now() - timedelta(seconds=1),
            }
        },
    )
    await db.agent_runs.update_one({"_id": run.id}, {"$set": {"status": "RUNNING"}})
    assert await make_worker(db).run_once() is True
    assert (await _get(db, run))["status"] == "SUCCEEDED"


async def test_live_lease_is_not_stolen(db, project, owner, register):
    register(lambda ctx: None)
    run = await _run(db, project, owner)
    await db.jobs.update_one(
        {"run_id": run.id},
        {
            "$set": {
                "status": "RUNNING",
                "lease_owner": "other",
                "lease_expires_at": now() + timedelta(seconds=60),
            }
        },
    )
    assert await make_worker(db).run_once() is False


async def test_crash_after_write_no_duplicate_revision(db, project, owner, register):
    calls = {"n": 0}

    async def agent(ctx):
        calls["n"] += 1
        rev = await artifacts.add_revision(
            ctx.db,
            ctx.project,
            "requirements",
            {"title": "r"},
            sources=ctx.run.inputs,
            origin="agent",
            summary="s",
            created_by=None,
            run_id=ctx.run.id,
        )
        if calls["n"] == 1:
            raise Crash()
        return agents.AgentOutcome(
            output={"artifact_id": str(rev.artifact_id), "revision": rev.revision},
            summary="ok",
            usage={},
        )

    register(agent)
    run = await _run(db, project, owner)
    with pytest.raises(Crash):
        await make_worker(db).run_once()
    await db.jobs.update_one(
        {"run_id": run.id}, {"$set": {"lease_expires_at": now() - timedelta(seconds=1)}}
    )
    assert await make_worker(db).run_once() is True
    assert await db.artifact_revisions.count_documents({"project_id": project.id}) == 1
    doc = await _get(db, run)
    assert doc["status"] == "SUCCEEDED" and doc["output"]["revision"] == 1


async def test_cancel_while_running(db, project, owner, register):
    started = asyncio.Event()

    async def agent(ctx):
        started.set()
        await asyncio.sleep(10)

    register(agent)
    run = await _run(db, project, owner)
    task = asyncio.create_task(make_worker(db, heartbeat_s=0.05).run_once())
    await started.wait()
    await cancel_run(db, project, run.id, owner)
    await asyncio.wait_for(task, 2)
    doc = await _get(db, run)
    assert doc["status"] == "CANCELLED"
    assert (await db.jobs.find_one({"run_id": run.id}))["status"] == "DONE"


async def test_two_workers_never_share_a_job(db, project, owner, register):
    seen = []

    async def agent(ctx):
        seen.append(ctx.run.id)
        await asyncio.sleep(0.1)
        return agents.AgentOutcome(output={}, summary="ok", usage={})

    register(agent)
    await _run(db, project, owner)
    w1, w2 = make_worker(db), Worker(db, provider_factory=MockProvider, worker_id="w2", poll_s=0.01)
    results = await asyncio.gather(w1.run_once(), w2.run_once())
    assert sorted(results) == [False, True] and len(seen) == 1


async def test_unexpected_error_fails_with_generic_message(db, project, owner, register):
    async def agent(ctx):
        raise RuntimeError("internal detail with secret-ish info")

    register(agent)
    run = await _run(db, project, owner)
    await make_worker(db).run_once()
    doc = await _get(db, run)
    assert doc["status"] == "FAILED" and doc["error"]["code"] == "INTERNAL"
    assert "secret" not in doc["error"]["message"]


async def test_unknown_agent_fails_the_run_instead_of_looping(db, project, owner):
    run = await _run(db, project, owner)
    await db.agent_runs.update_one({"_id": run.id}, {"$set": {"agent": "no_such_agent"}})
    assert await make_worker(db).run_once() is True
    doc = await _get(db, run)
    assert doc["status"] == "FAILED" and doc["error"]["code"] == "INTERNAL"
    assert await db.jobs.count_documents({"run_id": run.id, "status": "DONE"}) == 1


def test_rate_limited_retries_wait_out_the_minute_window():
    from app.worker.runner import retry_delay

    assert retry_delay("RATE_LIMITED", 1) >= 60
    assert retry_delay("TIMEOUT", 1) < 60
