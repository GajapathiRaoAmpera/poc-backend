import math
from decimal import ROUND_HALF_UP, Decimal

from app.modules.agents.budget.schema import (
    BudgetAgentOutput,
    BudgetContent,
    CostItem,
    Labour,
    MilestoneLabour,
    RoleLabour,
)
from app.modules.agents.plan.schema import PlanContent
from app.modules.projects.spec_schema import Budget
from app.modules.ratecard.models import RateCard
from app.modules.ratecard.service import snapshot

CENT = Decimal("0.01")


def _q(d: Decimal) -> Decimal:
    return d.quantize(CENT, ROUND_HALF_UP)


def _n(d: Decimal) -> str:
    return format(d.normalize(), "f")


def compute_budget(
    plan: PlanContent, card: RateCard, out: BudgetAgentOutput, budget_pref: Budget | None
) -> BudgetContent:
    hpd = card.hours_per_day
    days_by_role: dict[str, Decimal] = {}
    cost_by_ms: dict[str, list[Decimal]] = {m.id: [Decimal(0)] * 3 for m in plan.milestones}
    for t in plan.tasks:
        d = Decimal(str(t.effort_days))
        days_by_role[t.role] = days_by_role.get(t.role, Decimal(0)) + d
        rate = card.rate(t.role) or Decimal(0)
        acc = cost_by_ms.setdefault(t.milestone_id, [Decimal(0)] * 3)
        cost_by_ms[t.milestone_id] = [acc[0] + d, acc[1] + d * hpd, acc[2] + d * hpd * rate]

    warnings: list[str] = []
    by_role = []
    raw_labour = Decimal(0)  # totals use unrounded values; rows are display-rounded
    for role, days in days_by_role.items():
        rate = card.rate(role)
        if rate is None:
            warnings.append(f"No rate for role {role}; its {_n(days)} days are not costed")
        by_role.append(
            RoleLabour(
                role=role,
                days=days,
                hours=_q(days * hpd),
                rate=rate or Decimal(0),
                cost=_q(days * hpd * (rate or 0)),
            )
        )
        raw_labour += days * hpd * (rate or 0)
    titles = {m.id: m.title for m in plan.milestones}
    by_ms = [
        MilestoneLabour(
            milestone_id=m, title=titles.get(m, m), days=v[0], hours=_q(v[1]), cost=_q(v[2])
        )
        for m, v in cost_by_ms.items()
    ]
    labour_total = _q(raw_labour)

    items = [CostItem(**{**i.model_dump(), "amount": Decimal(str(i.amount))}) for i in out.items]
    months = max(1, math.ceil(plan.schedule.working_days / 21))
    non_labour = sum(
        (i.amount * (months if i.recurrence == "monthly" else 1) for i in items), Decimal(0)
    )
    subtotal = _q(labour_total + non_labour)
    pct = Decimal(str(out.contingency_pct))
    contingency = _q(subtotal * pct / 100)
    total = subtotal + contingency

    if budget_pref and budget_pref.currency and budget_pref.currency != card.currency:
        warnings.append(
            f"Budget range is in {budget_pref.currency}; the rate card is in {card.currency}"
        )
    elif budget_pref:
        lo = None if budget_pref.min is None else Decimal(str(budget_pref.min))
        hi = None if budget_pref.max is None else Decimal(str(budget_pref.max))
        if hi is not None and total > hi:
            warnings.append(f"Total {total} {card.currency} is above your budget range (max {hi})")
        if lo is not None and total < lo:
            warnings.append(f"Total {total} {card.currency} is below your budget range (min {lo})")

    return BudgetContent(
        summary=out.summary,
        rate_card=snapshot(card),
        labour=Labour(by_role=by_role, by_milestone=by_ms, total=labour_total),
        items=items,
        months=months,
        non_labour=_q(non_labour),
        contingency_pct=pct,
        contingency_reason=out.contingency_reason,
        contingency=contingency,
        subtotal=subtotal,
        total=total,
        currency=card.currency,
        warnings=warnings,
    )
