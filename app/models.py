"""Imports every model so Base.metadata is complete (used by Alembic and tests)."""

from app.core.models import Base
from app.modules.audit.models import AuditLog
from app.modules.auth.models import LoginAttempt, PasswordResetToken, Session, User
from app.modules.projects.models import Project, ProjectSpecification

__all__ = [
    "AuditLog",
    "Base",
    "LoginAttempt",
    "PasswordResetToken",
    "Project",
    "ProjectSpecification",
    "Session",
    "User",
]
