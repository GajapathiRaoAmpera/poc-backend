import re

from app.modules.agents.budget.schema import BudgetAgentOutput
from app.modules.artifacts.models import ValidationIssue


def _err(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(severity="error", code=code, message=message)


def budget_issues(
    out: BudgetAgentOutput, component_ids: set[str], risk_ids: set[str]
) -> list[ValidationIssue]:
    issues = [
        _err("UNKNOWN_REF", f"Item '{i.description}' references unknown component {i.component_id}")
        for i in out.items
        if i.component_id is not None and i.component_id not in component_ids
    ]
    if (
        out.contingency_pct > 0
        and not set(re.findall(r"R-\d{2}", out.contingency_reason)) & risk_ids
    ):
        issues.append(
            _err(
                "CONTINGENCY_REASON",
                "Contingency above 0% must cite at least one existing risk id (R-xx) in its reason",
            )
        )
    return issues
