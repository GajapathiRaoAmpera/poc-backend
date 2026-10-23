import asyncio
import uuid

import pytest

from app.core.errors import AppError
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.orchestration import runs
from app.modules.orchestration.models import RunStatus
from app.modules.orchestration.service import cancel_run, retry_run, start_stage
from app.modules.projects import service as projects
from app.modules.projects.spec_schema import ProjectSpecification as SpecData
from tests.factories import make_user
from tests.orchestration.conftest import add_spec


async def test_start_creates_run_job_and_event(db, project, owner):
    await add_spec(db, project)
    run, created = await start_stage(db, project, "requirements", "generate", owner)
    assert created and run.status == RunStatus.QUEUED and run.agent == "requirements_analyst"
    assert run.inputs == [SourceRef(kind="specification", revision=1)]
    job = await db.jobs.find_one({"run_id": run.id})
    assert job["status"] == "QUEUED" and job["max_attempts"] == 3
    ev = await db.run_events.find_one({"run_id": run.id})
    assert ev["type"] == "status" and "Queued" in ev["message"]


async def test_architecture_blocked_until_requirements_approved(db, project, owner):
    await add_spec(db, project)
    with pytest.raises(AppError) as e:
        await start_stage(db, project, "architecture", "generate", owner)
    assert e.value.code == "STAGE_BLOCKED"


async def test_architecture_inputs_are_approved_requirements(db, project, owner):
    await add_spec(db, project)
    await artifacts.add_revision(
        db,
        project,
        "requirements",
        {"title": "r"},
        sources=[SourceRef(kind="specification", revision=1)],
        origin="agent",
        summary="s",
        created_by=None,
    )
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    run, _ = await start_stage(db, project, "architecture", "generate", owner)
    art = await artifacts.get_artifact(db, project, "requirements")
    assert run.inputs == [SourceRef(kind="requirements", artifact_id=art.id, revision=1)]


async def test_duplicate_start_returns_active_run(db, project, owner):
    await add_spec(db, project)
    a, created_a = await start_stage(db, project, "requirements", "generate", owner)
    b, created_b = await start_stage(db, project, "requirements", "generate", owner)
    assert a.id == b.id and created_a and not created_b


async def test_concurrent_starts_create_one_run(db, project, owner):
    await add_spec(db, project)
    results = await asyncio.gather(
        *[start_stage(db, project, "requirements", "generate", owner) for _ in range(5)]
    )
    assert len({r.id for r, _ in results}) == 1
    assert await db.agent_runs.count_documents({}) == 1


async def test_quota(db, project, owner):
    await add_spec(db, project)
    for _ in range(50):
        await db.agent_runs.insert_one(
            {
                "_id": uuid.uuid4(),
                "project_id": project.id,
                "created_by": owner.id,
                "created_at": runs.now(),
                "status": "SUCCEEDED",
                "stage": "requirements",
            }
        )
    with pytest.raises(AppError) as e:
        await start_stage(db, project, "requirements", "generate", owner)
    assert e.value.code == "QUOTA_EXCEEDED" and e.value.status == 429


async def test_user_specific_limit(db, project):
    tight = await make_user(db, email="tight@x.com")
    await db.users.update_one({"_id": tight.id}, {"$set": {"daily_run_limit": 1}})
    tight.daily_run_limit = 1
    project.owner_id = tight.id
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", tight)
    await runs.transition(db, run.id, {RunStatus.QUEUED}, RunStatus.CANCELLED)
    with pytest.raises(AppError) as e:
        await start_stage(db, project, "requirements", "generate", tight)
    assert e.value.code == "QUOTA_EXCEEDED"


async def test_cancel_queued(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    out = await cancel_run(db, project, run.id, owner)
    assert out.status == RunStatus.CANCELLED
    assert (await db.jobs.find_one({"run_id": run.id}))["status"] == "DONE"


async def test_cancel_running_requests_cancel(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    await runs.transition(db, run.id, {RunStatus.QUEUED}, RunStatus.RUNNING)
    out = await cancel_run(db, project, run.id, owner)
    assert out.status == RunStatus.RUNNING and out.cancel_requested is True


async def test_cancel_finished_is_conflict(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    await cancel_run(db, project, run.id, owner)
    with pytest.raises(AppError) as e:
        await cancel_run(db, project, run.id, owner)
    assert e.value.status == 409


async def test_retry_only_from_failed_or_cancelled(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    with pytest.raises(AppError):
        await retry_run(db, project, run.id, owner)
    await cancel_run(db, project, run.id, owner)
    new = await retry_run(db, project, run.id, owner)
    assert new.id != run.id and new.inputs == run.inputs and new.mode == run.mode
    assert new.retry_of == run.id


async def test_spec_save_marks_requirements_stale(db, project, owner):
    await add_spec(db, project)
    await artifacts.add_revision(
        db,
        project,
        "requirements",
        {"title": "r"},
        sources=[SourceRef(kind="specification", revision=1)],
        origin="agent",
        summary="s",
        created_by=None,
    )
    await projects.save_specification(db, owner, project.id, SpecData())
    art = await artifacts.get_artifact(db, project, "requirements")
    assert art.stale and "Specification changed" in art.stale_reason
