import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.documents import Document, now
from app.modules.artifacts.models import SourceRef

RunMode = Literal["generate", "revise", "change_request"]
EventType = Literal["status", "step", "validation", "error"]


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE = {RunStatus.QUEUED, RunStatus.RUNNING}


class Attempt(BaseModel):
    n: int
    started_at: datetime
    finished_at: datetime | None = None
    outcome: str | None = None
    error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class RunError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class AgentRun(Document):
    """Collection ``agent_runs``. ``active`` is true while QUEUED/RUNNING (partial unique index)."""

    project_id: uuid.UUID
    owner_id: uuid.UUID
    stage: str
    agent: str
    agent_version: str = "1"
    mode: RunMode = "generate"
    status: RunStatus = RunStatus.QUEUED
    active: bool = True
    inputs: list[SourceRef] = []
    instruction: str | None = None
    base_revision: int | None = None
    output: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    is_mock: bool = False
    attempts: list[Attempt] = []
    usage: dict[str, int] = Field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    summary: str | None = None
    error: RunError | None = None
    cancel_requested: bool = False
    retry_of: uuid.UUID | None = None
    created_by: uuid.UUID
    created_at: datetime = Field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Job(Document):
    """Collection ``jobs``: the durable queue entry driving one agent run."""

    run_id: uuid.UUID
    project_id: uuid.UUID
    type: str = "agent_run"
    status: Literal["QUEUED", "RUNNING", "DONE"] = "QUEUED"
    attempts: int = 0
    max_attempts: int = 3
    run_after: datetime = Field(default_factory=now)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    timeout_s: float = 180
    last_error: str | None = None
    created_at: datetime = Field(default_factory=now)


class RunEvent(Document):
    """Collection ``run_events``: the transparent, ordered activity log per project."""

    project_id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    ts: datetime = Field(default_factory=now)
    type: EventType
    message: str
    data: dict[str, Any] | None = None
