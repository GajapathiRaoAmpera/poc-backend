from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.orchestration import runs
from app.modules.orchestration.models import RunStatus
from app.modules.orchestration.service import start_stage
from app.modules.orchestration.stages import stage_status, workflow
from tests.orchestration.conftest import add_spec


async def _req_revision(db, project, owner, **content):
    return await artifacts.add_revision(
        db,
        project,
        "requirements",
        {"title": "r", **content},
        sources=[SourceRef(kind="specification", revision=1)],
        origin="agent",
        summary="s",
        created_by=None,
    )


async def _status(db, project, stage="requirements"):
    return (await stage_status(db, project, stage)).status


async def test_requirements_blocked_without_spec(db, project):
    s = await stage_status(db, project, "requirements")
    assert s.status == "BLOCKED" and "specification" in s.reasons[0].lower()


async def test_not_started(db, project):
    await add_spec(db, project)
    assert await _status(db, project) == "NOT_STARTED"


async def test_queued_running_failed_cancelled(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    assert await _status(db, project) == "QUEUED"
    await runs.transition(db, run.id, {RunStatus.QUEUED}, RunStatus.RUNNING)
    assert await _status(db, project) == "RUNNING"
    await runs.transition(db, run.id, {RunStatus.RUNNING}, RunStatus.FAILED)
    assert await _status(db, project) == "FAILED"
    run2, _ = await start_stage(db, project, "requirements", "generate", owner)
    await runs.transition(db, run2.id, {RunStatus.QUEUED}, RunStatus.CANCELLED)
    assert await _status(db, project) == "CANCELLED"


async def test_failed_run_older_than_revision_does_not_mask_it(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    await runs.transition(db, run.id, {RunStatus.QUEUED}, RunStatus.RUNNING)
    await runs.transition(db, run.id, {RunStatus.RUNNING}, RunStatus.FAILED)
    await _req_revision(db, project, owner)
    assert await _status(db, project) == "WAITING_FOR_APPROVAL"


async def test_waiting_for_input(db, project, owner):
    await add_spec(db, project)
    await _req_revision(db, project, owner, needs_answer=True)
    assert await _status(db, project) == "WAITING_FOR_INPUT"


async def test_needs_revision_on_validation_error_or_changes_requested(db, project, owner):
    await add_spec(db, project)
    await _req_revision(db, project, owner, invalid=True)
    assert await _status(db, project) == "NEEDS_REVISION"
    await artifacts.add_revision(
        db,
        project,
        "requirements",
        {"title": "ok"},
        sources=[SourceRef(kind="specification", revision=1)],
        origin="user",
        summary="s",
        created_by=owner.id,
        base_revision=1,
    )
    assert await _status(db, project) == "WAITING_FOR_APPROVAL"
    await artifacts.request_changes(db, project, "requirements", 2, owner, "more detail")
    assert await _status(db, project) == "NEEDS_REVISION"


async def test_approved_and_architecture_unblocked(db, project, owner):
    await add_spec(db, project)
    await _req_revision(db, project, owner)
    arch = await stage_status(db, project, "architecture")
    assert arch.status == "BLOCKED" and "requirements" in arch.reasons[0].lower()
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    assert await _status(db, project) == "APPROVED"
    assert await _status(db, project, "architecture") == "NOT_STARTED"


async def test_stale_overrides(db, project, owner):
    await add_spec(db, project)
    await _req_revision(db, project, owner)
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    await add_spec(db, project, revision=2)
    await artifacts.mark_dependents_stale(db, project, "specification", 2)
    s = await stage_status(db, project, "requirements")
    assert s.status == "STALE" and "Specification changed" in s.reasons[0]


async def test_workflow_lists_all_stages(db, project):
    stages = await workflow(db, project)
    assert [s.stage for s in stages] == ["requirements", "architecture", "plan", "budget", "code"]
    assert [s.status for s in stages][2:] == ["BLOCKED", "BLOCKED", "BLOCKED"]
