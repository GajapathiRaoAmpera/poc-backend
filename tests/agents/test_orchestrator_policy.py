import uuid

import pytest

from app.modules.agents.orchestrator.policy import check_plan, default_plan
from app.modules.agents.orchestrator.schema import PlanContent
from app.modules.delivery.facts import Facts, StageFacts


def facts(**stage_over) -> Facts:
    stages = {s: StageFacts(stage=s) for s in ("requirements", "architecture")}
    for name, over in stage_over.items():
        for k, v in over.items():
            setattr(stages[name], k, v)
    return Facts(project_id=uuid.uuid4(), spec_revision=1, stages=stages)


def stage(name, include=True, inputs=(), approval=True):
    return {
        "stage": name,
        "include": include,
        "rationale": "because",
        "inputs": list(inputs),
        "approval_required": approval,
    }


def plan(**over) -> PlanContent:
    data = {
        "project_name": "Field app",
        "objective_summary": "Digitise job sheets",
        "project_type": "web_app",
        "brief": {"basics": {"name": "Field app"}},
        "stages": [stage("requirements"), stage("architecture", inputs=["requirements"])],
        "missing_info": [],
        "next_action": "run_stage",
        "next_stage": "requirements",
        "decision_summary": "Start with requirements.",
    }
    data.update(over)
    return PlanContent.model_validate(data)


def codes(p, f=None):
    return {i.code for i in check_plan(p, f or facts())}


def test_valid_plan_has_no_issues():
    assert codes(plan()) == set()


def test_unavailable_agent_included():
    p = plan(stages=[stage("requirements"), stage("budget", inputs=["requirements"])])
    assert "UNAVAILABLE_AGENT" in codes(p)


def test_unavailable_agent_excluded_is_fine():
    p = plan(stages=[stage("requirements"), stage("budget", include=False)])
    assert codes(p) == set()


def test_order_inputs_must_be_earlier_included_stages():
    p = plan(stages=[stage("requirements", inputs=["architecture"]), stage("architecture")])
    assert "ORDER" in codes(p)


def test_order_architecture_requires_requirements():
    p = plan(
        stages=[stage("requirements", include=False), stage("architecture")],
        next_stage="architecture",
    )
    assert "ORDER" in codes(p)


def test_approval_cannot_be_removed():
    p = plan(
        stages=[
            stage("requirements", approval=False),
            stage("architecture", inputs=["requirements"]),
        ]
    )
    assert "APPROVAL_REQUIRED" in codes(p)


def test_next_stage_must_be_included():
    p = plan(
        stages=[stage("requirements"), stage("architecture", include=False)],
        next_stage="architecture",
    )
    assert "NEXT_STAGE" in codes(p)


def test_next_stage_inputs_must_be_approved():
    p = plan(next_stage="architecture")
    assert "NEXT_STAGE" in codes(p)
    ok = facts(requirements={"has_draft": True, "approved_is_latest": True, "approved_revision": 1})
    assert "NEXT_STAGE" not in codes(p, ok)


def test_wait_approval_requires_a_pending_draft():
    assert "NEXT_STAGE" in codes(plan(next_action="wait_approval"))


def test_too_many_questions():
    qs = [
        {"id": f"I-{i:03d}", "question": "q", "why_it_matters": "w", "blocking": False}
        for i in range(1, 8)
    ]
    assert "TOO_MANY_QUESTIONS" not in codes(plan(missing_info=qs))
    with pytest.raises(ValueError):  # the schema itself caps at 7
        plan(missing_info=[*qs, {**qs[0], "id": "I-008"}])


def test_duplicate_stage_rejected():
    p = plan(stages=[stage("requirements"), stage("requirements")])
    assert "ORDER" in codes(p)


def test_default_plan_passes_policy():
    f = facts()
    assert check_plan(default_plan(f, None), f) == []


def test_default_plan_includes_all_implemented_with_approval():
    p = default_plan(facts(), None)
    included = {s.stage: s for s in p.stages if s.include}
    assert set(included) == {"requirements", "architecture"}
    assert all(s.approval_required for s in included.values())
    assert p.next_stage == "requirements"
