from app.modules.artifacts import service as artifacts
from tests.delivery.test_graph_c2 import _new_rates, _ready, _to_budget, approval


async def _to_code(db, h, owner, project):
    await _to_budget(db, h, owner, project)
    await artifacts.approve(db, project, "budget", 1, owner, None)
    assert await h.resume("approved:budget") == approval("code")


async def test_flow_to_done_through_code(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_code(db, h, owner, project)
    rev = await artifacts.get_revision(db, project, "code", 1)
    assert rev.validation.ok, rev.validation.issues
    assert {s.kind for s in rev.sources} == {"architecture", "plan"}
    await artifacts.approve(db, project, "code", 1, owner, None)
    assert await h.resume("approved:code") is None


async def test_architecture_change_marks_code_stale(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_code(db, h, owner, project)
    await artifacts.approve(db, project, "code", 1, owner, None)
    await h.resume("approved:code")
    arch = await artifacts.get_revision(db, project, "architecture", 1)
    await artifacts.add_revision(
        db, project, "architecture", arch.content, sources=arch.sources, origin="user",
        summary="tweak", created_by=owner.id, base_revision=1,
    )  # fmt: skip
    await artifacts.approve(db, project, "architecture", 2, owner, None)
    code = await artifacts.get_artifact(db, project, "code")
    assert code.stale and code.stale_reason


async def test_rates_change_keeps_code_fresh(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_code(db, h, owner, project)
    await artifacts.approve(db, project, "code", 1, owner, None)
    await h.resume("approved:code")
    await _new_rates(db, project, owner)
    assert await h.start("rates_changed") == approval("budget", 2)
    await artifacts.approve(db, project, "budget", 2, owner, None)
    assert await h.resume("approved:budget") is None
    assert not (await artifacts.get_artifact(db, project, "code")).stale
    assert len(await h.runs("code")) == 1
