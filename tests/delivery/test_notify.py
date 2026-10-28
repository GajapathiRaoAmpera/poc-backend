import pytest

from app.core.errors import AppError
from app.modules.delivery import service as delivery
from app.modules.delivery.threads import get_thread, set_thread
from tests.agents.helpers import setup_project


async def _thread(db, status, pending=None):
    owner, project = await setup_project(db)
    await delivery.start_delivery(db, project, owner)
    await db.jobs.delete_many({})
    await set_thread(db, project.id, status=status, pending=pending)
    return owner, project


async def _jobs(db):
    return [j async for j in db.jobs.find({"type": "graph_step", "status": "QUEUED"})]


async def test_duplicate_notify_single_job(db):
    _, project = await _thread(db, "PAUSED", {"kind": "approval", "stage": "requirements"})
    assert await delivery.notify(db, project, "approved:requirements") is True
    assert await delivery.notify(db, project, "approved:requirements") is False
    jobs = await _jobs(db)
    assert len(jobs) == 1 and jobs[0]["input"] == {
        "mode": "resume",
        "trigger": "approved:requirements",
        "message": None,
    }


async def test_notify_when_done_restarts(db):
    _, project = await _thread(db, "DONE")
    assert await delivery.notify(db, project, "approved:requirements") is True
    assert (await _jobs(db))[0]["input"]["mode"] == "start"


async def test_notify_while_running_noop_marks_dirty(db):
    _, project = await _thread(db, "RUNNING")
    assert await delivery.notify(db, project, "answers") is False
    assert await _jobs(db) == []
    assert (await get_thread(db, project)).dirty == "answers"
    await delivery.notify(db, project, "user_message")
    await delivery.notify(db, project, "answers")  # a re-plan trigger is never downgraded
    assert (await get_thread(db, project)).dirty == "user_message"


@pytest.mark.parametrize(
    "status,pending", [("FAILED", {"kind": "failed"}), ("PAUSED", {"kind": "cancelled"})]
)
async def test_notify_ignored_when_failed_or_cancelled(db, status, pending):
    _, project = await _thread(db, status, pending)
    assert await delivery.notify(db, project, "answers") is False


async def test_notify_without_thread_is_noop(db):
    _, project = await setup_project(db)
    assert await delivery.notify(db, project, "answers") is False


async def test_resume_rules(db):
    owner, project = await _thread(db, "RUNNING")
    with pytest.raises(AppError) as e:
        await delivery.resume(db, project, owner)
    assert e.value.code == "GRAPH_BUSY"
    await set_thread(db, project.id, status="DONE", pending=None)
    with pytest.raises(AppError) as e:
        await delivery.resume(db, project, owner)
    assert e.value.code == "NOT_PAUSED"
    await set_thread(db, project.id, status="PAUSED", pending={"kind": "needs_input"})
    await delivery.resume(db, project, owner)
    assert (await _jobs(db))[0]["input"]["trigger"] == "retry"


async def test_start_delivery_is_idempotent(db):
    owner, project = await setup_project(db)
    await delivery.start_delivery(db, project, owner)
    await delivery.start_delivery(db, project, owner)
    assert len(await _jobs(db)) == 1


@pytest.mark.parametrize("status,gate", [("FAILED", "failed"), ("DONE", "done")])
async def test_terminal_notices_carry_a_gate_for_styling(db, status, gate):
    _, project = await _thread(db, status)
    msg = await db.messages.find_one({"project_id": project.id, "role": "system"}, sort=[("seq", -1)])
    assert msg["refs"]["gate"] == gate
