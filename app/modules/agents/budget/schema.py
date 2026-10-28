from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CostItemOut(_M):
    component_id: Annotated[str, StringConstraints(max_length=60)] | None = None
    category: Literal["infra", "licence", "third_party", "other"]
    description: Short
    recurrence: Literal["one_off", "monthly"]
    # float (not Decimal) keeps the LLM json_schema plain; calc converts via Decimal(str(x)).
    amount: Annotated[float, Field(ge=0)]


class BudgetAgentOutput(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    items: Annotated[list[CostItemOut], Field(max_length=40)] = []
    contingency_pct: Annotated[float, Field(ge=0, le=30)]
    contingency_reason: Text


class CostItem(CostItemOut):
    amount: Decimal  # type: ignore[assignment]


class RoleLabour(_M):
    role: str
    days: Decimal
    hours: Decimal
    rate: Decimal
    cost: Decimal


class MilestoneLabour(_M):
    milestone_id: str
    title: str
    days: Decimal
    hours: Decimal
    cost: Decimal


class Labour(_M):
    by_role: list[RoleLabour]
    by_milestone: list[MilestoneLabour]
    total: Decimal


class BudgetContent(_M):
    summary: str
    rate_card: dict[str, Any]
    labour: Labour
    items: list[CostItem]
    months: int
    non_labour: Decimal
    contingency_pct: Decimal
    contingency_reason: str
    contingency: Decimal
    subtotal: Decimal
    total: Decimal
    currency: str
    warnings: list[str] = []
