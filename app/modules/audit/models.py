import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.core.documents import Document, now


class AuditLog(Document):
    """Collection ``audit_logs``."""

    actor_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=now)
