import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, Timestamps, UUIDPk


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"), nullable=False, default=ProjectStatus.DRAFT
    )


Index("ix_projects_owner_updated", Project.owner_id, Project.updated_at.desc())


class ProjectSpecification(UUIDPk, Timestamps, Base):
    """Append-only: a save creates revision n+1, existing rows are never updated."""

    __tablename__ = "project_specifications"
    __table_args__ = (UniqueConstraint("project_id", "revision"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
