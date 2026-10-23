import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.core.documents import Document, Timestamped, now


class UserRole(StrEnum):
    USER = "USER"
    SUPER_ADMIN = "SUPER_ADMIN"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class User(Timestamped):
    """Collection ``users``. ``email`` is stored lower-cased (unique index)."""

    email: str
    name: str
    password_hash: str
    role: UserRole = UserRole.USER
    status: UserStatus = UserStatus.ACTIVE
    daily_run_limit: int | None = None
    last_login_at: datetime | None = None


class Session(Document):
    """Collection ``sessions``. Only the SHA-256 of the cookie token is stored."""

    user_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    ip: str | None = None
    user_agent: str | None = None
    created_at: datetime = Field(default_factory=now)


class PasswordResetToken(Document):
    """Collection ``password_reset_tokens``."""

    user_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime = Field(default_factory=now)


class LoginAttempt(Document):
    """Collection ``login_attempts`` (TTL: 24 h)."""

    email: str
    ip: str
    succeeded: bool
    created_at: datetime = Field(default_factory=now)
