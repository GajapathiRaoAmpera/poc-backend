from app.modules.agents.budget.schema import BudgetAgentOutput
from app.modules.agents.budget.validate import budget_issues


def _out(component="C-1", pct=10.0, reason="Because R-01 may bite"):
    return BudgetAgentOutput.model_validate(
        {
            "summary": "s",
            "items": [
                {
                    "component_id": component,
                    "category": "infra",
                    "description": "d",
                    "recurrence": "monthly",
                    "amount": 5,
                }
            ],
            "contingency_pct": pct,
            "contingency_reason": reason,
        }
    )


def _codes(out):
    return [i.code for i in budget_issues(out, {"C-1"}, {"R-01"})]


def test_clean_and_null_component():
    assert _codes(_out()) == []
    assert _codes(_out(component=None)) == []


def test_unknown_component():
    assert _codes(_out(component="C-9")) == ["UNKNOWN_REF"]


def test_contingency_reason_must_cite_existing_risk():
    assert _codes(_out(reason="just because")) == ["CONTINGENCY_REASON"]
    assert _codes(_out(reason="see R-07")) == ["CONTINGENCY_REASON"]
    assert _codes(_out(pct=0, reason="")) == []
