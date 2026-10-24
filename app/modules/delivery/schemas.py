import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints

from app.modules.artifacts.models import ValidationIssue
from app.modules.auth.schemas import _Strict
from app.modules.delivery.plans import Plan
from app.modules.delivery.threads import DeliveryThread, describe
from app.modules.projects.schemas import ProjectOut
from app.modules.projects.spec_schema import Constraints

IntakeText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class IntakeIn(_Strict):
    message: IntakeText
    preferences: Constraints | None = None


class PlanOut(BaseModel):
    version: int
    content: dict[str, Any]
    issues: list[ValidationIssue]
    fallback: bool
    run_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def of(cls, p: Plan) -> "PlanOut":
        return cls(
            version=p.version,
            content=p.content,
            issues=p.validation.issues,
            fallback=p.fallback,
            run_id=p.run_id,
            created_at=p.created_at,
        )


class DeliveryOut(BaseModel):
    status: str
    pending: dict[str, Any] | None
    message: str
    plan: PlanOut | None
    last_error: str | None
    updated_at: datetime | None

    @classmethod
    def of(cls, t: DeliveryThread | None, plan: Plan | None) -> "DeliveryOut":
        if t is None:
            return cls(
                status="IDLE",
                pending=None,
                message="No delivery workflow yet",
                plan=None,
                last_error=None,
                updated_at=None,
            )
        return cls(
            status=t.status,
            pending=t.pending,
            message=describe(t.status, t.pending),
            plan=PlanOut.of(plan) if plan else None,
            last_error=t.last_error,
            updated_at=t.updated_at,
        )


class IntakeOut(BaseModel):
    project: ProjectOut
    delivery: DeliveryOut
