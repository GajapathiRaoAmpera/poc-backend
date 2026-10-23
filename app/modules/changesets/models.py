import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.core.documents import Document, now
from app.modules.artifacts.models import ValidationResult
from app.modules.diagrams.diff import Impact

ChangeSetStatus = Literal["PROPOSED", "APPLIED", "REJECTED", "INVALID", "SUPERSEDED"]


class ChangeSet(Document):
    """Collection ``change_sets``: an AI-proposed edit, applied only on user confirmation."""

    project_id: uuid.UUID
    owner_id: uuid.UUID
    artifact_id: uuid.UUID
    base_revision: int
    run_id: uuid.UUID | None = None
    instruction: str | None = None
    status: ChangeSetStatus = "PROPOSED"
    ops: list[dict[str, Any]]
    summary: str = ""
    impact: Impact
    validation: ValidationResult = ValidationResult()
    applied_revision: int | None = None
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=now)

    def to_doc(self) -> dict[str, Any]:
        doc = super().to_doc()
        doc["impact"] = self.impact.model_dump()
        doc["validation"] = {"issues": [i.model_dump() for i in self.validation.issues]}
        if self.run_id is None:
            doc.pop("run_id")  # sparse unique index
        return doc
