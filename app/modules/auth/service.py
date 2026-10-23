import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Unauthenticated
from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    validate_password,
    verify_password,
)
from app.modules.auth.models import LoginAttempt, Session, User, UserStatus
from app.modules.auth.schemas import LoginIn, RegisterIn

SESSION_TTL = timedelta(days=7)
SESSION_EXTEND_AFTER = timedelta(hours=1)
INVALID_CREDENTIALS = "Incorrect email or password"


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


async def authenticate(db: AsyncSession, data: LoginIn, ip: str) -> User:
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
