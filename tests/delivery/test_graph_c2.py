from app.llm.mock import MockProvider
from app.modules.artifacts import service as artifacts
from app.modules.delivery import service as delivery
from app.modules.delivery.routing import NO_RATES
from app.modules.delivery.threads import get_thread
from app.modules.ratecard.models import RateCard
from app.modules.ratecard.service import SEED, effective, save_default, save_override, seed_default
from app.worker.runner import Worker
from tests.agents.helpers import good_architecture, good_requirements, setup_project
from tests.agents.test_budget_agent import budget
from tests.agents.test_plan_agent import plan
from tests.delivery.conftest import scripted

RISK = {"id": "R-01", "description": "Slip", "impact": "high", "mitigation": "Buffer"}
SCRIPT = scripted(
    requirements_output=good_requirements(open_questions=[]),
    architecture_output=good_architecture(),
    plan_output=plan(risks=[RISK]),
    budget_output=budget(),
)


def approval(stage, revision=1):
    return {"kind": "approval", "stage": stage, "payload": {"stage": stage, "revision": revision}}


async def _ready(db, harness):
    await seed_default(db)
    owner, project = await setup_project(db)
    return owner, project, harness(project, SCRIPT)


async def _to_budget(db, h, owner, project):
    """Walk the graph to the budget approval gate."""
    assert await h.start() == approval("requirements")
    for stage, nxt in (("requirements", "architecture"), ("architecture", "plan")):
        await artifacts.approve(db, project, stage, 1, owner, None)
        assert await h.resume(f"approved:{stage}") == approval(nxt)
    await artifacts.approve(db, project, "plan", 1, owner, None)
    assert await h.resume("approved:plan") == approval("budget")


async def _new_rates(db, project, owner, rate="100"):
    card = (await effective(db, project)).model_dump(mode="json")
    card["roles"] = [{**r, "hourly_rate": rate} for r in card["roles"]]
    await save_override(db, project, owner, RateCard.model_validate(card))


async def _worker_flow(db, stop_at=None):
    """Run the real worker flow, approving each stage, until ``stop_at`` would run or DONE."""
    await seed_default(db)
    owner, project = await setup_project(db)
    provider = MockProvider(script=SCRIPT)
    w = Worker(db, provider_factory=lambda: provider, heartbeat_s=0.05, poll_s=0.01)
    await delivery.start_delivery(db, project, owner)
    await w.run_once()
    for stage in ("requirements", "architecture", "plan", "budget", "code"):
        if stage == stop_at:
            break
        t = await get_thread(db, project)
        assert (t.status, t.pending["kind"], t.pending["stage"]) == ("PAUSED", "approval", stage)
        await artifacts.approve(db, project, stage, 1, owner, None)
        await delivery.notify(db, project, f"approved:{stage}")
        await w.run_once()
    return owner, project, w, provider


async def test_full_flow(db):
    _, project, _, _ = await _worker_flow(db)
    assert (await get_thread(db, project)).status == "DONE"


async def test_default_rates_resume_empty_card_gate(db):
    owner, project, w, _ = await _worker_flow(db, stop_at="plan")
    # The plan gate is still pending: empty the card, then approve the plan.
    await db.settings.update_one({"_id": "rate_card"}, {"$set": {"roles": []}})
    await artifacts.approve(db, project, "plan", 1, owner, None)
    await delivery.notify(db, project, "approved:plan")
    await w.run_once()
    t = await get_thread(db, project)
    assert (t.pending["kind"], t.pending["payload"]["reason"]) == ("needs_input", NO_RATES)
    await save_default(db, owner, SEED)
    await w.run_once()
    t = await get_thread(db, project)
    assert (t.pending["kind"], t.pending["stage"]) == ("approval", "budget")
    assert [r["status"] async for r in db.agent_runs.find({"stage": "budget"})] == ["SUCCEEDED"]


async def test_team_change_via_preferences_reschedules_without_replan(db):
    from app.modules.projects.router import save_preferences
    from app.modules.projects.spec_schema import Constraints

    owner, project, w, provider = await _worker_flow(db)
    orch = sum(c["schema_name"] == "orchestrator_output" for c in provider.calls)
    await save_preferences(project.id, Constraints(team_size={"backend_engineer": 2}), owner, db)
    job = await db.jobs.find_one({"type": "graph_step", "status": "QUEUED"})
    assert job["input"]["trigger"] == "schedule_changed"
    await w.run_once()
    assert sum(c["schema_name"] == "orchestrator_output" for c in provider.calls) == orch
    modes = [r["mode"] async for r in db.agent_runs.find({"stage": "plan"}).sort("created_at", 1)]
    assert modes == ["generate", "reschedule"]
    t = await get_thread(db, project)
    assert (t.pending["kind"], t.pending["stage"]) == ("approval", "plan")


async def test_rates_change_reprices(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_budget(db, h, owner, project)
    await artifacts.approve(db, project, "budget", 1, owner, None)
    assert await h.resume("approved:budget") == approval("code")
    calls = len(h.provider.calls)
    await _new_rates(db, project, owner)
    assert await h.start("rates_changed") == approval("budget", 2)
    assert len(h.provider.calls) == calls
    runs = await h.runs("budget")
    assert [r["mode"] for r in runs] == ["generate", "reprice"]
    assert runs[-1]["status"] == "SUCCEEDED"


async def test_rates_reverted_is_not_work(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_budget(db, h, owner, project)
    await artifacts.approve(db, project, "budget", 1, owner, None)
    await h.resume("approved:budget")
    await artifacts.approve(db, project, "code", 1, owner, None)
    await h.resume("approved:code")
    await _new_rates(db, project, owner)  # stale: "Rates changed"
    await save_override(db, project, owner, None)  # back to the default the budget used
    assert await h.start("rates_changed") is None
    assert len(await h.runs("budget")) == 1
    assert not (await artifacts.get_artifact(db, project, "budget")).stale


async def test_architecture_change_cascades(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_budget(db, h, owner, project)
    await artifacts.approve(db, project, "budget", 1, owner, None)
    await h.resume("approved:budget")
    arch = await artifacts.get_revision(db, project, "architecture", 1)
    await artifacts.add_revision(
        db, project, "architecture", arch.content, sources=arch.sources, origin="user",
        summary="tweak", created_by=owner.id, base_revision=1,
    )  # fmt: skip
    await artifacts.approve(db, project, "architecture", 2, owner, None)
    assert (await artifacts.get_artifact(db, project, "plan")).stale
    assert await h.start("approved:architecture") == approval("plan", 2)
    assert [r["mode"] for r in await h.runs("plan")] == ["generate", "generate"]
    await artifacts.approve(db, project, "plan", 2, owner, None)
    assert await h.resume("approved:plan") == approval("budget", 2)
    assert [r["mode"] for r in await h.runs("budget")] == ["generate", "generate"]


async def test_empty_rate_card_gate(db, harness):
    owner, project, h = await _ready(db, harness)
    assert await h.start() == approval("requirements")
    for stage, nxt in (("requirements", "architecture"), ("architecture", "plan")):
        await artifacts.approve(db, project, stage, 1, owner, None)
        assert await h.resume(f"approved:{stage}") == approval(nxt)
    await artifacts.approve(db, project, "plan", 1, owner, None)
    await db.settings.update_one({"_id": "rate_card"}, {"$set": {"roles": []}})
    pending = await h.resume("approved:plan")
    assert pending["kind"] == "needs_input" and pending["stage"] == "budget"
    assert pending["payload"]["reason"] == "Set rates before the budget can be calculated"
    assert await h.runs("budget") == []


async def test_rate_change_before_budget_exists(db, harness):
    owner, project, h = await _ready(db, harness)
    assert await h.start() == approval("requirements")
    for stage, nxt in (("requirements", "architecture"), ("architecture", "plan")):
        await artifacts.approve(db, project, stage, 1, owner, None)
        assert await h.resume(f"approved:{stage}") == approval(nxt)
    await _new_rates(db, project, owner)
    assert await h.resume("rates_changed") == approval("plan")
    assert await artifacts.get_artifact(db, project, "budget") is None
    assert await h.runs("budget") == []


async def test_team_change_reschedules(db, harness):
    from app.modules.projects.service import save_preferences

    owner, project, h = await _ready(db, harness)
    await _to_budget(db, h, owner, project)
    await artifacts.approve(db, project, "budget", 1, owner, None)
    await h.resume("approved:budget")
    calls = len(h.provider.calls)
    await save_preferences(db, owner, project.id, {"start_date": "2026-11-02"})
    assert await h.start("schedule_changed") == approval("plan", 2)
    assert [r["mode"] for r in await h.runs("plan")] == ["generate", "reschedule"]
    assert len(h.provider.calls) == calls  # no re-plan, no LLM call


async def test_plan_change_survives_rates_revert(db, harness):
    owner, project, h = await _ready(db, harness)
    await _to_budget(db, h, owner, project)
    await artifacts.approve(db, project, "budget", 1, owner, None)
    await h.resume("approved:budget")
    plan1 = await artifacts.get_revision(db, project, "plan", 1)
    await artifacts.add_revision(
        db, project, "plan", plan1.content, sources=plan1.sources, origin="user",
        summary="edit", created_by=owner.id, base_revision=1,
    )  # fmt: skip
    await artifacts.approve(db, project, "plan", 2, owner, None)  # budget stale: plan changed
    await _new_rates(db, project, owner)  # reason overwritten: "Rates changed"
    await save_override(db, project, owner, None)  # rates reverted
    assert (await artifacts.get_artifact(db, project, "budget")).stale
    assert await h.start("rates_changed") == approval("budget", 2)
    assert [r["mode"] for r in await h.runs("budget")] == ["generate", "generate"]
