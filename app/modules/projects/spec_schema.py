"""Structured project specification captured by the creation wizard.

Every field is optional so incomplete submissions are allowed; missing items are
treated as assumptions downstream.
"""

from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SHORT = 300
LONG = 5000
MAX_ITEMS = 50

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=SHORT)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, max_length=LONG)]
ShortList = Annotated[list[Short], Field(max_length=MAX_ITEMS)]
LongList = Annotated[list[Long], Field(max_length=MAX_ITEMS)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Basics(_Section):
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] | None = None
    description: Long | None = None


class Problem(_Section):
    business_problem: Long | None = None
    target_users: ShortList | None = None
    goals: ShortList | None = None
    expected_outcomes: ShortList | None = None


class Requirements(_Section):
    functional: LongList | None = None
    non_functional: LongList | None = None
    expected_scale: Short | None = None


class Budget(_Section):
    min: Annotated[float, Field(ge=0)] | None = None
    max: Annotated[float, Field(ge=0)] | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None

    @model_validator(mode="after")
    def _min_not_above_max(self) -> Self:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Budget minimum must not exceed maximum")
        return self


class Constraints(_Section):
    preferred_technologies: ShortList | None = None
    deployment_preference: Short | None = None
    budget: Budget | None = None
    target_delivery_date: date | None = None
    team_assumptions: Long | None = None
    compliance: ShortList | None = None
    existing_integrations: ShortList | None = None
    start_date: date | None = None
    team_size: (
        Annotated[dict[str, Annotated[int, Field(ge=1, le=20)]], Field(max_length=20)] | None
    ) = None


class ProjectSpecification(_Section):
    basics: Basics | None = None
    problem: Problem | None = None
    requirements: Requirements | None = None
    constraints: Constraints | None = None


# Schedule inputs: they reschedule the plan (preferences mark it stale), not re-open requirements.
SCHEDULE_ONLY = ("start_date", "team_size")


def requirements_scope(data: dict) -> dict:
    """The part of a specification the requirements depend on (all but the schedule inputs)."""
    constraints = data.get("constraints") or {}
    return {**data, "constraints": {k: v for k, v in constraints.items() if k not in SCHEDULE_ONLY}}
