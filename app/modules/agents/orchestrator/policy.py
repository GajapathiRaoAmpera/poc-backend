"""Deterministic policy every orchestrator plan must satisfy before the graph acts on it."""

from app.modules.agents.orchestrator.schema import PlanContent, StagePlan
from app.modules.artifacts.models import ValidationIssue
from app.modules.delivery.facts import Facts
from app.modules.projects.spec_schema import ProjectSpecification

IMPLEMENTED = {"requirements", "architecture", "plan", "budget", "code"}
ORDER = ["requirements", "architecture", "plan", "budget", "code"]
REQUIRED_APPROVAL = {"requirements", "architecture", "plan", "budget", "code"}
DEPENDS_ON = {
    "architecture": ["requirements"],
    "plan": ["requirements", "architecture"],
    "budget": ["plan", "architecture"],
    "code": ["architecture", "plan"],
}
MAX_QUESTIONS = 7
# What actually reaches the client per turn, and how many answered rounds before it must proceed.
MAX_QUESTIONS_PER_TURN = 3
NO_BLOCKING_AFTER_ROUNDS = 2
NO_QUESTIONS_AFTER_ROUNDS = 3


def _err(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(severity="error", code=code, message=message)


def _done(facts: Facts, stage: str) -> bool:
    s = facts.stages.get(stage)
    return bool(s and s.approved_is_latest and not s.stale)


def check_plan(plan: PlanContent, facts: Facts) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    seen: set[str] = set()
    included: list[str] = []
    for s in plan.stages:
        if s.stage in seen:
            out.append(_err("ORDER", f"Stage {s.stage} is listed more than once"))
            continue
        seen.add(s.stage)
        if not s.include:
            continue
        if s.stage not in IMPLEMENTED:
            out.append(
                _err("UNAVAILABLE_AGENT", f"{s.stage} has no agent yet; set include to false")
            )
        earlier = {x for x in included if ORDER.index(x) < ORDER.index(s.stage)}
        bad = [i for i in s.inputs if i not in earlier]
        if bad:
            out.append(_err("ORDER", f"{s.stage} inputs {bad} are not earlier included stages"))
        missing = [d for d in DEPENDS_ON.get(s.stage, []) if d not in included]
        if missing:
            out.append(_err("ORDER", f"{s.stage} requires {missing} to be included before it"))
        if s.stage in REQUIRED_APPROVAL and not s.approval_required:
            out.append(_err("APPROVAL_REQUIRED", f"{s.stage} always requires human approval"))
        included.append(s.stage)

    if plan.next_action == "run_stage":
        if plan.next_stage not in included:
            out.append(_err("NEXT_STAGE", f"next_stage {plan.next_stage} is not an included stage"))
        else:
            unready = [d for d in DEPENDS_ON.get(plan.next_stage, []) if not _done(facts, d)]
            if unready:
                out.append(
                    _err("NEXT_STAGE", f"{plan.next_stage} cannot run before {unready} is approved")
                )
    if plan.next_action == "wait_approval" and not any(
        facts.stages.get(s) and facts.stages[s].has_draft and not facts.stages[s].approved_is_latest
        for s in included
    ):
        out.append(_err("NEXT_STAGE", "wait_approval but no draft is waiting for approval"))
    if len(plan.missing_info) > MAX_QUESTIONS:
        out.append(_err("TOO_MANY_QUESTIONS", f"Ask at most {MAX_QUESTIONS} questions per turn"))
    return out


def limit_questions(plan: PlanContent, facts: Facts) -> PlanContent:
    """Bound the questions asked, so intake converges instead of interrogating the client."""
    if facts.answered_rounds >= NO_QUESTIONS_AFTER_ROUNDS:
        questions = []
    else:
        ordered = sorted(plan.missing_info, key=lambda q: not q.blocking)
        questions = ordered[:MAX_QUESTIONS_PER_TURN]
        if facts.answered_rounds >= NO_BLOCKING_AFTER_ROUNDS:
            questions = [q.model_copy(update={"blocking": False}) for q in questions]
    if questions == plan.missing_info:
        return plan
    return plan.model_copy(update={"missing_info": questions})


def default_plan(facts: Facts, brief: ProjectSpecification | None) -> PlanContent:
    """The safe plan used when the orchestrator's own plan fails policy after a repair."""
    stages = [
        StagePlan(
            stage=s,
            include=s in IMPLEMENTED,
            rationale="Default plan" if s in IMPLEMENTED else "No agent is available yet",
            inputs=[d for d in DEPENDS_ON.get(s, []) if d in IMPLEMENTED]
            if s in IMPLEMENTED
            else [],
            approval_required=s in IMPLEMENTED,
        )
        for s in ORDER
    ]
    next_action, next_stage = "done", None
    for s in (x for x in ORDER if x in IMPLEMENTED):
        f = facts.stages.get(s)
        if f is None or not f.has_draft or f.stale:
            next_action, next_stage = "run_stage", s
            break
        if not f.approved_is_latest:
            next_action = "wait_approval"
            break
    name = (
        brief.basics.name if brief and brief.basics and brief.basics.name else None
    ) or "Project"
    return PlanContent(
        project_name=name,
        objective_summary=(brief.basics.description if brief and brief.basics else None) or "",
        project_type="other",
        brief=brief or ProjectSpecification(),
        stages=stages,
        missing_info=[],
        next_action=next_action,
        next_stage=next_stage,
        decision_summary="Default plan: every available stage, each approved by you.",
    )
