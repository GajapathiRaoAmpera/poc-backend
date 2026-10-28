import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.documents import Document, now

Origin = Literal["agent", "user", "change_set", "restore", "system"]
Decision = Literal["APPROVED", "CHANGES_REQUESTED"]


class SourceRef(BaseModel):
    """Which upstream revision an artifact revision was built from."""

    kind: str  # "specification" or an artifact kind
    artifact_id: uuid.UUID | None = None
    revision: int


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    path: str | None = None


class ValidationResult(BaseModel):
    issues: list[ValidationIssue] = []

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


class Artifact(Document):
    """Collection ``artifacts``: one per (project, kind)."""

    project_id: uuid.UUID
    owner_id: uuid.UUID
    kind: str
    latest_revision: int = 0
    approved_revision: int | None = None
    stale: bool = False
    stale_reason: str | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Revision(Document):
    """Collection ``artifact_revisions``. Immutable: never updated after insert."""

    artifact_id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    kind: str
    revision: int
    schema_version: str
    content: dict[str, Any]
    sources: list[SourceRef] = []
    origin: Origin
    run_id: uuid.UUID | None = None
    change_set_id: uuid.UUID | None = None
    restored_from: int | None = None
    summary: str = ""
    validation: ValidationResult = ValidationResult()
    created_by: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=now)

    def to_doc(self) -> dict[str, Any]:
        doc = super().to_doc()
        doc["validation"] = {"issues": [i.model_dump() for i in self.validation.issues]}
        doc["sources"] = [s.model_dump() for s in self.sources]
        if self.run_id is None:
            doc.pop("run_id")  # sparse unique index: absent, not null
        return doc


class Approval(Document):
    """Collection ``approvals``: every decision, in order."""

    project_id: uuid.UUID
    artifact_id: uuid.UUID
    kind: str
    revision: int
    decision: Decision
    comment: str | None = None
    decided_by: uuid.UUID
    decided_at: datetime = Field(default_factory=now)
