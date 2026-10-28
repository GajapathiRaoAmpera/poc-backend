from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
RefId = Annotated[str, StringConstraints(max_length=20)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Milestone(_M):
    id: Annotated[str, StringConstraints(pattern=r"^M-\d{2}$")]
    title: Short
    goal: Text


class Task(_M):
    id: Annotated[str, StringConstraints(pattern=r"^T-\d{3}$")]
    title: Short
    milestone_id: RefId
    role: Annotated[str, StringConstraints(max_length=40)]
    # float (not Decimal) keeps the LLM json_schema plain; budget converts via Decimal(str(x)).
    effort_days: Annotated[float, Field(ge=0.5, le=20)]
    depends_on: Annotated[list[RefId], Field(max_length=30)] = []
    requirement_ids: Annotated[list[RefId], Field(max_length=30)] = []
    component_ids: Annotated[
        list[Annotated[str, StringConstraints(max_length=60)]], Field(max_length=30)
    ] = []
    # Set by a person on the timeline ("start no earlier than"); the scheduler respects it.
    not_before: date | None = None

    @field_validator("effort_days")
    @classmethod
    def _half_days(cls, v: float) -> float:
        if v * 2 != int(v * 2):
            raise ValueError("effort_days must be a multiple of 0.5")
        return v


class Risk(_M):
    id: Annotated[str, StringConstraints(pattern=r"^R-\d{2}$")]
    description: Text
    impact: Literal["low", "medium", "high"]
    mitigation: Text


class PlanAgentOutput(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    milestones: Annotated[list[Milestone], Field(min_length=1, max_length=12)]
    tasks: Annotated[list[Task], Field(min_length=1, max_length=150)]
    risks: Annotated[list[Risk], Field(max_length=10)] = []


class ScheduledTask(Task):
    start: date
    end: date


class ScheduledMilestone(Milestone):
    start: date
    end: date


class Schedule(_M):
    start: date
    end: date
    working_days: int
    target_date: date | None = None


class PlanContent(PlanAgentOutput):
    milestones: list[ScheduledMilestone]  # type: ignore[assignment]
    tasks: list[ScheduledTask]  # type: ignore[assignment]
    schedule: Schedule
    warnings: list[str] = []
