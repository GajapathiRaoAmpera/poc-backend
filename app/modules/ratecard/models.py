from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

Role = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")]


class RoleRate(BaseModel):
    role: Role
    hourly_rate: Annotated[Decimal, Field(gt=0, le=10000)]


class RateCard(BaseModel):
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    hours_per_day: Annotated[Decimal, Field(ge=1, le=12)]
    roles: Annotated[list[RoleRate], Field(min_length=1, max_length=20)]

    @field_validator("hours_per_day")
    @classmethod
    def _half_hours(cls, v: Decimal) -> Decimal:
        if (v * 2) % 1:
            raise ValueError("hours_per_day must be in steps of 0.5")
        return v

    @model_validator(mode="after")
    def _unique_roles(self) -> "RateCard":
        names = self.role_names()
        if len(set(names)) != len(names):
            raise ValueError("Roles must be unique")
        return self

    def rate(self, role: str) -> Decimal | None:
        return next((r.hourly_rate for r in self.roles if r.role == role), None)

    def role_names(self) -> list[str]:
        return [r.role for r in self.roles]
