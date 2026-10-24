import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from app.core.documents import Document, Timestamped, now


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class Project(Timestamped):
    """Collection ``projects``."""

    owner_id: uuid.UUID
    name: str
    description: str | None = None
    status: ProjectStatus = ProjectStatus.DRAFT
    # "intake": placeholder taken from the first intake message; the orchestrator may rename it.
    name_source: Literal["user", "intake", "orchestrator"] = "user"
    # Optional user preferences (the spec's ``Constraints`` shape); override the orchestrator.
    preferences: dict[str, Any] | None = None


class ProjectSpecification(Document):
    """Collection ``project_specifications``. Append-only: never updated after insert."""

    project_id: uuid.UUID
    revision: int
    data: dict[str, Any]
    origin: Literal["user", "orchestrator"] = "user"
    created_by: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=now)
