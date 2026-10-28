import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.core.documents import Document, now

Channel = Literal["intake", "requirements", "architecture", "plan", "budget", "code"]
STAGE_CHANNELS = ("requirements", "architecture", "plan", "budget", "code")
Role = Literal["user", "agent", "system"]


class Conversation(Document):
    """Collection ``conversations``: one per (project, channel)."""

    project_id: uuid.UUID
    owner_id: uuid.UUID
    channel: Channel
    created_at: datetime = Field(default_factory=now)


class Message(Document):
    """Collection ``messages``. Append-only. ``channel`` is denormalised for cheap reads."""

    conversation_id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    channel: Channel
    seq: int
    role: Role
    agent: str | None = None
    text: str
    refs: dict[str, Any] = {}
    created_at: datetime = Field(default_factory=now)
