from datetime import date
from decimal import Decimal

from app.modules.agents.budget.calc import compute_budget
from app.modules.agents.budget.schema import BudgetAgentOutput, BudgetContent
from app.modules.agents.plan.schema import PlanContent
from app.modules.projects.spec_schema import Budget
from app.modules.ratecard.models import RateCard, RoleRate

D = Decimal


def _plan(*tasks, working_days=10):
    d = date(2026, 11, 2)
    return PlanContent.model_validate(
        {
            "summary": "s",
            "milestones": [
                {"id": "M-01", "title": "One", "goal": "g", "start": d, "end": d},
                {"id": "M-02", "title": "Two", "goal": "g", "start": d, "end": d},
            ],
            "tasks": [
                {
                    "id": f"T-{i:03}",
                    "title": "t",
                    "milestone_id": ms,
                    "role": role,
                    "effort_days": eff,
                    "start": d,
                    "end": d,
                }
                for i, (role, eff, ms) in enumerate(tasks, 1)
            ],
            "risks": [],
            "schedule": {"start": d, "end": d, "working_days": working_days},
        }
    )


def _card(hpd="8", currency="USD", **rates):
    rates = rates or {"backend": "65", "qa": "50"}
    return RateCard(
        currency=currency,
        hours_per_day=D(hpd),
        roles=[RoleRate(role=r, hourly_rate=D(p)) for r, p in rates.items()],
    )


def _out(items=(), pct=0.0):
    return BudgetAgentOutput.model_validate(
        {"summary": "s", "items": list(items), "contingency_pct": pct, "contingency_reason": "R-01"}
    )


BASIC = (("backend", 2.5, "M-01"), ("qa", 1, "M-02"))


def test_labour_exact():
    c = compute_budget(_plan(*BASIC), _card(), _out(), None)
    by = {r.role: r for r in c.labour.by_role}
    assert (by["backend"].hours, by["backend"].cost) == (D("20"), D("1300.00"))
    assert (by["qa"].hours, by["qa"].cost) == (D("8"), D("400.00"))
    assert c.labour.total == D("1700.00")
    ms = {m.milestone_id: m for m in c.labour.by_milestone}
    assert ms["M-01"].cost == D("1300.00") and ms["M-02"].title == "Two"


def test_hours_per_day_changes_cost():
    c = compute_budget(_plan(("backend", 2.5, "M-01")), _card("7.5"), _out(), None)
    assert c.labour.by_role[0].hours == D("18.75")
    assert c.labour.by_role[0].cost == D("1218.75")


def test_months_and_total():
    items = [
        {
            "component_id": None,
            "category": "infra",
            "description": "a",
            "recurrence": "one_off",
            "amount": 1000,
        },
        {
            "component_id": None,
            "category": "infra",
            "description": "b",
            "recurrence": "monthly",
            "amount": 200,
        },
    ]
    c = compute_budget(_plan(*BASIC, working_days=43), _card(), _out(items, 10), None)
    assert c.months == 3
    assert (c.non_labour, c.subtotal) == (D("1600"), D("3300.00"))
    assert (c.contingency, c.total) == (D("330.00"), D("3630.00"))
    assert c.currency == "USD"


def test_rounding_half_up_on_totals_only():
    # 0.5 d x 1 h x 0.01 = 0.005 -> 0.01 (half-up; banker's would give 0.00)
    c = compute_budget(_plan(("backend", 0.5, "M-01")), _card("1", backend="0.01"), _out(), None)
    assert c.labour.by_role[0].cost == D("0.01")
    assert c.labour.total == D("0.01") and c.total == D("0.01")


def test_range_warning_same_currency():
    items = [
        {
            "component_id": None,
            "category": "infra",
            "description": "a",
            "recurrence": "one_off",
            "amount": 1000,
        }
    ]
    c = compute_budget(
        _plan(*BASIC, working_days=43),
        _card(),
        _out(items + [{**items[0], "recurrence": "monthly", "amount": 200}], 10),
        Budget(max=3000, currency="USD"),
    )
    assert any("above your budget range" in w for w in c.warnings)
    c = compute_budget(_plan(*BASIC), _card(), _out(), Budget(min=5000))
    assert any("below your budget range" in w for w in c.warnings)
    c = compute_budget(_plan(*BASIC), _card(), _out(), Budget(min=1000, max=5000))
    assert c.warnings == []


def test_range_currency_mismatch():
    c = compute_budget(_plan(*BASIC), _card(), _out(), Budget(max=100, currency="EUR"))
    assert c.warnings == ["Budget range is in EUR; the rate card is in USD"]


def test_role_missing_from_card():
    c = compute_budget(_plan(("astronaut", 2, "M-01"), ("qa", 1, "M-01")), _card(), _out(), None)
    assert c.labour.total == D("400.00")
    assert c.warnings == ["No rate for role astronaut; its 2 days are not costed"]


def test_round_trip():
    c = compute_budget(_plan(*BASIC), _card(), _out(), None)
    dumped = c.model_dump(mode="json")
    assert dumped["total"] == "1700.00"
    assert BudgetContent.model_validate(dumped) == c


def test_display_rows_quantized_to_cents():
    c = compute_budget(_plan(("backend", 2.5, "M-01")), _card("7.5"), _out(), None)
    dumped = c.model_dump(mode="json")
    assert dumped["labour"]["by_role"][0]["cost"] == "1218.75"
    assert dumped["labour"]["by_milestone"][0]["cost"] == "1218.75"
    assert dumped["labour"]["by_role"][0]["hours"] == "18.75"
    assert dumped["non_labour"] == "0.00"
