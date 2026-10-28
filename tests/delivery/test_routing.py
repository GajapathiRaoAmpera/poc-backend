import uuid

import pytest

from app.modules.agents.orchestrator.policy import default_plan
from app.modules.delivery.facts import ARTIFACT_STAGES, Facts, StageFacts
from app.modules.delivery.routing import needs_replan, route


def facts(**stage_over) -> Facts:
    stages = {s: StageFacts(stage=s) for s in ARTIFACT_STAGES}
    for name, over in stage_over.items():
        for k, v in over.items():
            setattr(stages[name], k, v)
    return Facts(project_id=uuid.uuid4(), spec_revision=1, stages=stages)


DRAFT = {"has_draft": True, "latest_revision": 2}
APPROVED = {**DRAFT, "approved_revision": 2, "approved_is_latest": True}


def plan(f, architecture=True, missing=()):
    p = default_plan(f, None)
    for s in p.stages:
        if s.stage in ("architecture", "plan", "budget"):  # plan and budget need architecture
            s.include = architecture
    p.missing_info = list(missing)
    return p


def go(**stage_over):
    f = facts(**stage_over)
    return route(plan(f), f, {})


def test_nothing_yet_runs_requirements_generate():
    d = go()
    assert (d.node, d.mode) == ("requirements", "generate")


def test_blocking_missing_info_gates_intake_questions():
    from app.modules.agents.orchestrator.schema import MissingInfo

    f = facts()
    q = MissingInfo(id="I-001", question="Who uses it?", why_it_matters="scope", blocking=True)
    d = route(plan(f, missing=[q]), f, {})
    assert (d.node, d.gate_kind) == ("gate", "intake_questions")
    assert d.payload["questions"][0]["id"] == "I-001"


def test_non_blocking_missing_info_does_not_gate():
    from app.modules.agents.orchestrator.schema import MissingInfo

    f = facts()
    q = MissingInfo(id="I-001", question="Brand colours?", why_it_matters="ui", blocking=False)
    assert route(plan(f, missing=[q]), f, {}).node == "requirements"


def test_active_run_gates_stage_busy():
    d = go(requirements={"active_run": True})
    assert (d.node, d.gate_kind, d.payload["stage"]) == ("gate", "stage_busy", "requirements")


def test_blocking_questions_gate_awaiting_answers():
    d = go(requirements={**DRAFT, "blocking_unanswered": 2})
    assert (d.node, d.gate_kind) == ("gate", "awaiting_answers")


def test_answers_trigger_revise():
    d = go(requirements={**DRAFT, "answered_since_agent": True})
    assert (d.node, d.mode) == ("requirements", "revise")


def test_validation_errors_trigger_revise():
    d = go(requirements={**DRAFT, "save_errors": 1})
    assert (d.node, d.mode) == ("requirements", "revise")


def test_draft_waits_for_approval():
    d = go(requirements=DRAFT)
    assert (d.node, d.gate_kind, d.payload) == (
        "gate",
        "approval",
        {"stage": "requirements", "revision": 2},
    )


def test_requirements_changes_requested_revise_with_comment():
    d = go(requirements={**DRAFT, "changes_requested": "Add SSO"})
    assert (d.node, d.mode, d.instruction) == ("requirements", "revise", "Add SSO")


def test_requirements_approved_runs_architecture():
    d = go(requirements=APPROVED)
    assert (d.node, d.mode) == ("architecture", "generate")


def test_architecture_changes_requested_regenerates_with_comment():
    d = go(requirements=APPROVED, architecture={**DRAFT, "changes_requested": "Use queues"})
    assert (d.node, d.mode, d.instruction) == ("architecture", "generate", "Use queues")


def test_all_approved_ends():
    stages = dict.fromkeys(("requirements", "architecture", "plan", "budget"), APPROVED)
    assert go(**stages).node == "end"


def test_approved_architecture_runs_plan():
    d = go(requirements=APPROVED, architecture=APPROVED)
    assert (d.node, d.mode) == ("plan", "generate")


def _c2(plan_over=None, budget_over=None, roles=6):
    f = facts(
        requirements=APPROVED,
        architecture=APPROVED,
        plan={**APPROVED, **(plan_over or {})},
        budget={**APPROVED, **(budget_over or {})},
    )
    f.rate_card_roles = roles
    return route(plan(f), f, {})


def test_stale_plan_with_same_sources_reschedules():
    d = _c2(plan_over={"stale": True, "reschedule_only": True})
    assert (d.node, d.mode) == ("plan", "reschedule")
    assert _c2(plan_over={"stale": True}).mode == "generate"


def test_stale_budget_with_same_sources_reprices():
    d = _c2(budget_over={"stale": True, "reprice_only": True})
    assert (d.node, d.mode) == ("budget", "reprice")
    assert _c2(budget_over={"stale": True}).mode == "generate"


def test_empty_rate_card_gates_budget():
    d = _c2(budget_over={"stale": True}, roles=0)
    assert (d.gate_kind, d.payload["stage"]) == ("needs_input", "budget")
    assert d.payload["reason"] == "Set rates before the budget can be calculated"


def test_architecture_excluded_ends_after_requirements():
    f = facts(requirements=APPROVED)
    assert route(plan(f, architecture=False), f, {}).node == "end"


def test_stale_requirements_rerun_before_approved_architecture():
    d = go(requirements={**APPROVED, "stale": True}, architecture=APPROVED)
    assert (d.node, d.mode) == ("requirements", "revise")


def test_stale_architecture_regenerates():
    d = go(requirements=APPROVED, architecture={**APPROVED, "stale": True})
    assert (d.node, d.mode) == ("architecture", "generate")


@pytest.mark.parametrize("loops,node", [(2, "requirements"), (3, "gate")])
def test_loop_budget(loops, node):
    f = facts()
    d = route(plan(f), f, {"requirements": loops})
    assert d.node == node
    if node == "gate":
        assert d.gate_kind == "needs_input"


@pytest.mark.parametrize(
    "trigger,has_plan,last_event,expected",
    [
        (None, False, None, True),
        ("intake", True, None, True),
        ("user_message", True, None, True),
        ("approved:requirements", True, None, True),
        ("approved:architecture", True, None, False),
        (None, True, {"stage": "requirements", "outcome": "failed"}, True),
        (None, True, {"stage": "requirements", "outcome": "succeeded"}, False),
        ("answers", True, None, False),
    ],
)
def test_needs_replan(trigger, has_plan, last_event, expected):
    assert needs_replan(trigger, has_plan, last_event) is expected
