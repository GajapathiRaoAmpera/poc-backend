import copy

from app.modules.agents.requirements.service import save_answers
from app.modules.artifacts import service as artifacts
from app.modules.projects.service import add_spec_revision
from tests.agents.helpers import (
    SPEC,
    approved_requirements,
    good_requirements,
    run_stage,
    setup_project,
)


def _spec(**constraints):
    data = copy.deepcopy(SPEC)
    data["constraints"] = {**(data.get("constraints") or {}), **constraints}
    return data


async def _stale(db, project):
    return (await artifacts.get_artifact(db, project, "requirements")).stale


async def test_schedule_only_spec_change_keeps_requirements_current(db):
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    await add_spec_revision(db, project, _spec(team_size={"backend_engineer": 2}), created_by=None)
    await add_spec_revision(db, project, _spec(start_date="2026-11-02"), created_by=None)
    assert not await _stale(db, project)
    await add_spec_revision(db, project, _spec(deployment_preference="Azure"), created_by=None)
    assert await _stale(db, project)


async def test_answers_after_schedule_only_change_are_not_stale(db):
    owner, project = await setup_project(db)
    await run_stage(db, project, owner, "requirements", lambda *a: good_requirements())
    await add_spec_revision(db, project, _spec(team_size={"backend_engineer": 2}), created_by=None)
    rev = await save_answers(db, project, owner, {"Q-001": "Azure AD"}, base_revision=1)
    assert not await _stale(db, project)
    # A real brief change still makes the next requirements revision stale.
    await add_spec_revision(db, project, _spec(deployment_preference="Azure"), created_by=None)
    edit = await artifacts.get_revision(db, project, "requirements", rev.revision)
    await artifacts.add_revision(
        db, project, "requirements", edit.content, sources=edit.sources, origin="user",
        summary="edit", created_by=owner.id, base_revision=rev.revision,
    )  # fmt: skip
    assert await _stale(db, project)
