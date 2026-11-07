from app.modules.agents.testing.schema import TestPlan
from app.modules.artifacts.models import ValidationIssue


def must_ids(requirements: dict) -> set[str]:
    return {r["id"] for r in requirements.get("requirements", []) if r.get("priority") == "MUST"}


def coverage_issues(plan: TestPlan, requirements: dict) -> list[ValidationIssue]:
    known = {r["id"] for r in requirements.get("requirements", [])}
    issues = [
        ValidationIssue(
            severity="error",
            code="REQ_UNKNOWN",
            message=f"{c.id}: unknown requirement {c.requirement_id}",
            path=c.id,
        )
        for c in plan.cases
        if c.requirement_id not in known
    ]
    if missing := sorted(must_ids(requirements) - {c.requirement_id for c in plan.cases}):
        issues.append(
            ValidationIssue(
                severity="error",
                code="MUST_UNCOVERED",
                message=f"MUST requirements without a test case: {', '.join(missing)}",
                refs=missing,
            )
        )
    if len({c.id for c in plan.cases}) != len(plan.cases):
        issues.append(ValidationIssue(severity="error", code="DUP_CASE", message="Duplicate ids"))
    return issues
