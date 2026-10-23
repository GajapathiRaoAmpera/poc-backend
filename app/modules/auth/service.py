import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, RateLimited, Unauthenticated, ValidationFailed
from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    validate_password,
    verify_password,
)
from app.modules.auth.models import LoginAttempt, PasswordResetToken, Session, User, UserStatus
from app.modules.auth.schemas import ChangePasswordIn, LoginIn, RegisterIn, ResetConfirmIn

logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(days=7)
SESSION_EXTEND_AFTER = timedelta(hours=1)
INVALID_CREDENTIALS = "Incorrect email or password"
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW = timedelta(minutes=15)
RESET_TTL = timedelta(minutes=30)
INVALID_RESET = "This reset link is invalid or has expired"


def _now() -> datetime:
    return datetime.now(UTC)


async def create_session(db: AsyncSession, user: User, ip: str | None, ua: str | None) -> str:
    """Create a session and return the raw token (only its hash is stored)."""
    token = new_token()
    db.add(
        Session(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=_now() + SESSION_TTL,
            ip=ip,
            user_agent=(ua or "")[:500] or None,
        )
    )
    await db.commit()
    return token


async def revoke_sessions(
    db: AsyncSession, user_id: uuid.UUID, except_token_hash: str | None = None
) -> None:
    stmt = delete(Session).where(Session.user_id == user_id)
    if except_token_hash:
        stmt = stmt.where(Session.token_hash != except_token_hash)
    await db.execute(stmt)
    await db.commit()


async def revoke_session(db: AsyncSession, raw_token: str) -> None:
    await db.execute(delete(Session).where(Session.token_hash == hash_token(raw_token)))
    await db.commit()


async def user_for_token(db: AsyncSession, raw_token: str | None) -> tuple[User, Session]:
    """Resolve a cookie token to an active user, sliding the expiry. Raises Unauthenticated."""
    if not raw_token:
        raise Unauthenticated()
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == hash_token(raw_token))
        )
    ).first()
    now = _now()
    if row is None:
        raise Unauthenticated()
    session, user = row
    if session.expires_at <= now or user.status != UserStatus.ACTIVE:
        raise Unauthenticated("Your session has expired")
    if (now + SESSION_TTL) - session.expires_at >= SESSION_EXTEND_AFTER:
        session.expires_at = now + SESSION_TTL
        await db.commit()
    return user, session


async def register(db: AsyncSession, data: RegisterIn) -> User:
    validate_password(data.password, data.email)
    user = User(email=data.email, name=data.name, password_hash=hash_password(data.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("An account with this email already exists") from exc
    await db.refresh(user)
    return user


async def _recent_failures(db: AsyncSession, email: str, ip: str) -> int:
    since = _now() - FAILED_LOGIN_WINDOW
    stmt = select(func.count()).where(
        LoginAttempt.email == email,
        LoginAttempt.ip == ip,
        LoginAttempt.succeeded.is_(False),
        LoginAttempt.created_at > since,
    )
    return (await db.execute(stmt)).scalar_one()


async def authenticate(db: AsyncSession, data: LoginIn, ip: str) -> User:
    if await _recent_failures(db, data.email, ip) >= MAX_FAILED_LOGINS:
        raise RateLimited("Too many attempts. Try again in 15 minutes.")
    user = (await db.execute(select(User).where(User.email == data.email))).scalar_one_or_none()
    ok = (
        user is not None
        and verify_password(data.password, user.password_hash)
        and user.status == UserStatus.ACTIVE
    )
    db.add(LoginAttempt(email=data.email, ip=ip, succeeded=ok))
    if not ok:
        await db.commit()
        raise Unauthenticated(INVALID_CREDENTIALS)
    user.last_login_at = _now()
    await db.commit()
    return user


async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Issue a reset token if the account exists. Callers always answer 202."""
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or user.status != UserStatus.ACTIVE:
        return
    token = new_token()
    db.add(
        PasswordResetToken(
            user_id=user.id, token_hash=hash_token(token), expires_at=_now() + RESET_TTL
        )
    )
    await db.commit()
    settings = get_settings()
    if settings.environment == "development":
        # No email provider yet (spec open item). Never enabled outside development.
        logger.info("Password reset link: %s/reset-password?token=%s", settings.app_origin, token)


async def confirm_password_reset(db: AsyncSession, data: ResetConfirmIn) -> None:
    row = (
        await db.execute(
            select(PasswordResetToken, User)
            .join(User, User.id == PasswordResetToken.user_id)
            .where(PasswordResetToken.token_hash == hash_token(data.token))
            .with_for_update()
        )
    ).first()
    if row is None:
        raise ValidationFailed(INVALID_RESET)
    reset, user = row
    if reset.used_at is not None or reset.expires_at <= _now():
        raise ValidationFailed(INVALID_RESET)
    validate_password(data.password, user.email)
    user.password_hash = hash_password(data.password)
    reset.used_at = _now()
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=_now())
    )
    await db.commit()
    await revoke_sessions(db, user.id)


async def change_password(
    db: AsyncSession, user: User, data: ChangePasswordIn, keep_token_hash: str
) -> None:
    if not verify_password(data.current_password, user.password_hash):
        raise ValidationFailed("Current password is incorrect")
    validate_password(data.new_password, user.email)
    user.password_hash = hash_password(data.new_password)
    await db.commit()
    await revoke_sessions(db, user.id, except_token_hash=keep_token_hash)
