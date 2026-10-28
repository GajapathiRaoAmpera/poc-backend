"""Pure routing: given the validated plan and fresh facts, which graph node runs next."""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.modules.agents.orchestrator.policy import IMPLEMENTED, ORDER
from app.modules.agents.orchestrator.schema import PlanContent
from app.modules.delivery.facts import Facts, StageFacts

MAX_AUTO_RUNS = 3

Node = Literal["requirements", "architecture", "plan", "budget", "gate", "end"]
NO_RATES = "Set rates before the budget can be calculated"


@dataclass
class Decision:
    node: Node
    mode: Literal["generate", "revise", "reschedule", "reprice"] | None = None
    instruction: str | None = None
    gate_kind: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def _gate(kind: str, **payload: Any) -> Decision:
    return Decision(node="gate", gate_kind=kind, payload=payload)


def _run(f: StageFacts, facts: Facts, loops: dict[str, int], instruction: str | None) -> Decision:
    stage = f.stage
    if loops.get(stage, 0) >= MAX_AUTO_RUNS:
        return _gate(
            "needs_input",
            stage=stage,
            reason=f"The {stage} agent ran {MAX_AUTO_RUNS} times without your input",
        )
    if stage == "budget" and facts.rate_card_roles == 0:
        return _gate("needs_input", stage=stage, reason=NO_RATES)
    # The Architecture Agent has no revise mode: it regenerates, steered by the instruction.
    mode = "revise" if f.has_draft and stage == "requirements" else "generate"
    if instruction is None and f.reschedule_only:
        mode = "reschedule"
    if instruction is None and f.reprice_only:
        mode = "reprice"
    return Decision(node=stage, mode=mode, instruction=instruction)


def route(plan: PlanContent, facts: Facts, loops: dict[str, int]) -> Decision:
    blocking = [q.model_dump() for q in plan.missing_info if q.blocking]
    if blocking:
        return _gate("intake_questions", questions=blocking)
    included = {s.stage for s in plan.stages if s.include and s.stage in IMPLEMENTED}
    for stage in (s for s in ORDER if s in included):
        f = facts.stages[stage]
        if f.active_run:
            return _gate("stage_busy", stage=stage)
        if not f.has_draft or f.stale or f.changes_requested:
            return _run(f, facts, loops, f.changes_requested)
        if f.blocking_unanswered:
            return _gate("awaiting_answers", stage=stage, count=f.blocking_unanswered)
        if f.answered_since_agent or f.save_errors:
            return _run(f, facts, loops, None)
        if not f.approved_is_latest:
            return _gate("approval", stage=stage, revision=f.latest_revision)
    return Decision(node="end")


def needs_replan(trigger: str | None, has_plan: bool, last_event: dict | None) -> bool:
    """Call the (LLM) planner only when the plan may need to change; otherwise just route."""
    if not has_plan or trigger in ("intake", "user_message", "approved:requirements"):
        return True
    return bool(last_event and last_event.get("outcome") == "failed")
