import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.artifacts.models import SourceRef, ValidationIssue
from app.modules.orchestration.models import AgentRun, Attempt, RunError, RunEvent

StageName = Literal["requirements", "architecture", "plan", "budget", "code"]


class StartRunIn(BaseModel):
    mode: Literal["generate", "revise"] = "generate"


class RunEventOut(BaseModel):
    seq: int
    run_id: uuid.UUID
    ts: datetime
    type: str
    message: str
    data: dict[str, Any] | None = None

    @classmethod
    def of(cls, e: RunEvent) -> "RunEventOut":
        return cls(seq=e.seq, run_id=e.run_id, ts=e.ts, type=e.type, message=e.message, data=e.data)


class RunOut(BaseModel):
    id: uuid.UUID
    stage: str
    agent: str
    agent_version: str
    mode: str
    status: str
    inputs: list[SourceRef]
    instruction: str | None
    base_revision: int | None
    output: dict[str, Any] | None
    provider: str | None
    model: str | None
    is_mock: bool
    attempts: list[Attempt]
    usage: dict[str, int]
    summary: str | None
    error: RunError | None
    cancel_requested: bool
    retry_of: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, r: AgentRun) -> "RunOut":
        return cls.model_validate(r.model_dump())


class RunDetailOut(RunOut):
    events: list[RunEventOut] = []


class RevisionSummaryOut(BaseModel):
    id: uuid.UUID
    kind: str
    revision: int
    origin: str
    summary: str
    run_id: uuid.UUID | None
    change_set_id: uuid.UUID | None
    restored_from: int | None
    sources: list[SourceRef]
    issues: list[ValidationIssue]
    ok: bool
    created_by: uuid.UUID | None
    created_at: datetime


class RevisionOut(RevisionSummaryOut):
    schema_version: str
    content: dict[str, Any]


class ArtifactOut(BaseModel):
    id: uuid.UUID
    kind: str
    latest_revision: int
    approved_revision: int | None
    stale: bool
    stale_reason: str | None
    updated_at: datetime
    revision: RevisionOut | None = None


class StageStateOut(BaseModel):
    stage: str
    status: str
    reasons: list[str] = Field(default_factory=list)
    available: bool
    artifact: ArtifactOut | None = None
    latest_run: RunOut | None = None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    kind: str
    revision: int
    decision: str
    comment: str | None
    decided_by: uuid.UUID
    decided_at: datetime


def revision_summary(rev) -> RevisionSummaryOut:
    return RevisionSummaryOut(
        id=rev.id,
        kind=rev.kind,
        revision=rev.revision,
        origin=rev.origin,
        summary=rev.summary,
        run_id=rev.run_id,
        change_set_id=rev.change_set_id,
        restored_from=rev.restored_from,
        sources=rev.sources,
        issues=rev.validation.issues,
        ok=rev.validation.ok,
        created_by=rev.created_by,
        created_at=rev.created_at,
    )


def revision_out(rev) -> RevisionOut:
    return RevisionOut(
        **revision_summary(rev).model_dump(), schema_version=rev.schema_version, content=rev.content
    )


def artifact_out(art, rev=None) -> ArtifactOut:
    return ArtifactOut(
        id=art.id,
        kind=art.kind,
        latest_revision=art.latest_revision,
        approved_revision=art.approved_revision,
        stale=art.stale,
        stale_reason=art.stale_reason,
        updated_at=art.updated_at,
        revision=revision_out(rev) if rev else None,
    )


def approval_out(a) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        kind=a.kind,
        revision=a.revision,
        decision=a.decision,
        comment=a.comment,
        decided_by=a.decided_by,
        decided_at=a.decided_at,
    )
