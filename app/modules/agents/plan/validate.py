from collections import Counter

from app.modules.agents.plan.schema import PlanAgentOutput
from app.modules.artifacts.models import ValidationIssue


def _err(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(severity="error", code=code, message=message)


def _find_cycle(deps: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    stack: list[str] = []

    def dfs(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for d in sorted(deps.get(n, [])):
            if d not in deps:
                continue
            if state.get(d) == 1:
                return [*stack[stack.index(d) :], d]
            if d not in state and (c := dfs(d)):
                return c
        stack.pop()
        state[n] = 2
        return None

    for n in sorted(deps):
        if n not in state and (c := dfs(n)):
            return c
    return None


def plan_issues(
    out: PlanAgentOutput,
    requirement_ids: set[str],
    must_ids: set[str],
    component_ids: set[str],
    roles: list[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for kind, items in (("milestone", out.milestones), ("task", out.tasks), ("risk", out.risks)):
        for i, n in Counter(x.id for x in items).items():
            if n > 1:
                issues.append(_err("DUPLICATE_ID", f"Duplicate {kind} id {i}"))
    mids = {m.id for m in out.milestones}
    tids = {t.id for t in out.tasks}
    for t in out.tasks:
        refs = [
            (t.milestone_id, mids, "milestone"),
            *((d, tids, "task") for d in t.depends_on),
            *((r, requirement_ids, "requirement") for r in t.requirement_ids),
            *((c, component_ids, "component") for c in t.component_ids),
        ]
        for ref, known, what in refs:
            if ref not in known:
                issues.append(_err("UNKNOWN_REF", f"Task {t.id} references unknown {what} {ref}"))
        if t.role not in roles:
            issues.append(
                _err(
                    "UNKNOWN_ROLE",
                    f"Task {t.id} has unknown role {t.role}. Allowed roles: {', '.join(roles)}",
                )
            )
    if cycle := _find_cycle({t.id: t.depends_on for t in out.tasks}):
        issues.append(_err("CYCLE", "Dependency cycle: " + " → ".join(cycle)))
    covered = {r for t in out.tasks for r in t.requirement_ids}
    for r in sorted(must_ids - covered):
        issues.append(_err("MUST_UNCOVERED", f"MUST requirement {r} is not covered by any task"))
    return issues
