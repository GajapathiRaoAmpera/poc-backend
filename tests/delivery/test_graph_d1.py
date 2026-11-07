from app.modules.agents.orchestrator.policy import ORDER, default_plan
from app.modules.agents.orchestrator.schema import PlanContent
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import Artifact, Revision, SourceRef
from app.modules.delivery.facts import Facts, StageFacts
from app.modules.delivery.routing import route
from tests.delivery.test_graph_c2 import _new_rates, _ready, approval
from tests.delivery.test_graph_c3 import _to_code


async def _to_design(db, h, owner, project):
    await _to_code(db, h, owner, project)
    await artifacts.approve(db, project, "code", 1, owner, None)
    assert await h.resume("approved:code") == approval("design")


async def test_flow_through_design_after_code(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_design(db, h, owner, project)
    rev = await artifacts.get_revision(db, project, "design", 1)
    assert {s.kind for s in rev.sources} == {"requirements", "architecture"}
    await artifacts.approve(db, project, "design", 1, owner, None)
    # the default test plan leaves implementation out, so the flow ends here
    assert await h.resume("approved:design") is None
    assert await h.runs("implementation") == []


async def test_route_stops_before_deploy(monkeypatch):
    monkeypatch.setattr(
        "app.modules.delivery.routing.IMPLEMENTED", set(ORDER)
    )  # every agent exists
    done = StageFacts(
        stage="x", has_draft=True, latest_revision=1, approved_revision=1, approved_is_latest=True
    )
    facts = Facts(
        project_id=None,
        spec_revision=1,
        stages={s: done if s != "deploy" else StageFacts(stage=s) for s in ORDER},
        rate_card_roles=1,
    )
    plan = default_plan(facts, None).model_dump()
    for s in plan["stages"]:
        s.update(include=True, approval_required=True)
    assert route(PlanContent.model_validate(plan), facts, {}).node == "end"


async def _fake_artifact(db, project, kind, sources):
    """A stored revision for a kind that has no agent yet, built on the given source revisions."""
    art = Artifact(project_id=project.id, owner_id=project.owner_id, kind=kind, latest_revision=1)
    await db.artifacts.insert_one(art.to_doc())
    rev = Revision(
        artifact_id=art.id, project_id=project.id, owner_id=project.owner_id, kind=kind,
        revision=1, schema_version="1.0", content={}, origin="agent",
        sources=[SourceRef(kind=k, revision=r) for k, r in sources],
    )  # fmt: skip
    await db.artifact_revisions.insert_one(rev.to_doc())


async def test_code_change_marks_implementation_and_testing_stale(db, harness):
    owner, project, h = await _ready(db, harness)
    await _fake_artifact(db, project, "implementation", [("code", 1), ("design", 1)])
    await _fake_artifact(
        db, project, "testing", [("implementation", 1), ("design", 1), ("code", 1)]
    )
    await artifacts.mark_dependents_stale(db, project, "code", 2)
    assert (await artifacts.get_artifact(db, project, "implementation")).stale
    assert (await artifacts.get_artifact(db, project, "testing")).stale


async def test_rates_change_keeps_design_fresh(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_design(db, h, owner, project)
    await artifacts.approve(db, project, "design", 1, owner, None)
    await h.resume("approved:design")
    await _new_rates(db, project, owner)
    assert await h.start("rates_changed") == approval("budget", 2)
    await artifacts.approve(db, project, "budget", 2, owner, None)
    assert await h.resume("approved:budget") is None
    assert not (await artifacts.get_artifact(db, project, "design")).stale
    assert len(await h.runs("design")) == 1


def test_graph_job_timeout_covers_long_stages():
    from app.modules.delivery.service import graph_job_timeout
    from app.modules.orchestration.service import job_timeout

    assert graph_job_timeout() >= max(job_timeout("implementation"), job_timeout("testing"))
