import logging
import uuid
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.db import Db
from app.core.documents import now
from app.core.errors import Conflict, RateLimited, Unauthenticated, ValidationFailed
from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    validate_password,
    verify_password,
)
from app.modules.auth.models import (
    LoginAttempt,
    PasswordResetToken,
    Session,
    User,
    UserStatus,
)
from app.modules.auth.schemas import ChangePasswordIn, LoginIn, RegisterIn, ResetConfirmIn

logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(days=7)
SESSION_EXTEND_AFTER = timedelta(hours=1)
INVALID_CREDENTIALS = "Incorrect email or password"
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW = timedelta(minutes=15)
RESET_TTL = timedelta(minutes=30)
INVALID_RESET = "This reset link is invalid or has expired"


async def find_user_by_email(db: Db, email: str) -> User | None:
    doc = await db.users.find_one({"email": email})
    return User.from_doc(doc) if doc else None


async def create_session(db: Db, user: User, ip: str | None, ua: str | None) -> str:
    """Create a session and return the raw token (only its hash is stored)."""
    token = new_token()
    session = Session(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now() + SESSION_TTL,
        ip=ip,
        user_agent=(ua or "")[:500] or None,
    )
    await db.sessions.insert_one(session.to_doc())
    return token


async def revoke_sessions(
    db: Db, user_id: uuid.UUID, except_token_hash: str | None = None, session=None
) -> None:
    query: dict = {"user_id": user_id}
    if except_token_hash:
        query["token_hash"] = {"$ne": except_token_hash}
    await db.sessions.delete_many(query, session=session)


async def revoke_session(db: Db, raw_token: str) -> None:
    await db.sessions.delete_one({"token_hash": hash_token(raw_token)})


async def user_for_token(db: Db, raw_token: str | None) -> tuple[User, Session]:
    """Resolve a cookie token to an active user, sliding the expiry. Raises Unauthenticated."""
    if not raw_token:
        raise Unauthenticated()
    doc = await db.sessions.find_one({"token_hash": hash_token(raw_token)})
    if doc is None:
        raise Unauthenticated()
    session = Session.from_doc(doc)
    t = now()
    # The TTL monitor runs about once a minute, so check expiry explicitly too.
    if session.expires_at <= t:
        raise Unauthenticated("Your session has expired")
    user_doc = await db.users.find_one({"_id": session.user_id})
    if user_doc is None:
        raise Unauthenticated("Your session has expired")
    user = User.from_doc(user_doc)
    if user.status != UserStatus.ACTIVE:
        raise Unauthenticated("Your session has expired")
    if (t + SESSION_TTL) - session.expires_at >= SESSION_EXTEND_AFTER:
        session.expires_at = t + SESSION_TTL
        await db.sessions.update_one(
            {"_id": session.id}, {"$set": {"expires_at": session.expires_at}}
        )
    return user, session


async def register(db: Db, data: RegisterIn) -> User:
    validate_password(data.password, data.email)
    user = User(email=data.email, name=data.name, password_hash=hash_password(data.password))
    try:
        await db.users.insert_one(user.to_doc())
    except DuplicateKeyError as exc:
        raise Conflict("An account with this email already exists") from exc
    return user


async def _recent_failures(db: Db, email: str, ip: str) -> int:
    return await db.login_attempts.count_documents(
        {
            "email": email,
            "ip": ip,
            "succeeded": False,
            "created_at": {"$gt": now() - FAILED_LOGIN_WINDOW},
        }
    )


async def authenticate(db: Db, data: LoginIn, ip: str) -> User:
    if await _recent_failures(db, data.email, ip) >= MAX_FAILED_LOGINS:
        raise RateLimited("Too many attempts. Try again in 15 minutes.")
    user = await find_user_by_email(db, data.email)
    ok = (
        user is not None
        and verify_password(data.password, user.password_hash)
        and user.status == UserStatus.ACTIVE
    )
    attempt = LoginAttempt(email=data.email, ip=ip, succeeded=ok)
    await db.login_attempts.insert_one(attempt.to_doc())
    if not ok or user is None:
        raise Unauthenticated(INVALID_CREDENTIALS)
    user.last_login_at = now()
    await db.users.update_one({"_id": user.id}, {"$set": {"last_login_at": user.last_login_at}})
    return user


async def request_password_reset(db: Db, email: str) -> None:
    """Issue a reset token if the account exists. Callers always answer 202."""
    user = await find_user_by_email(db, email)
    if user is None or user.status != UserStatus.ACTIVE:
        return
    token = new_token()
    reset = PasswordResetToken(
        user_id=user.id, token_hash=hash_token(token), expires_at=now() + RESET_TTL
    )
    await db.password_reset_tokens.insert_one(reset.to_doc())
    settings = get_settings()
    if settings.environment == "development":
        # No email provider yet (spec open item). Never enabled outside development.
        logger.info("Password reset link: %s/reset-password?token=%s", settings.app_origin, token)


async def confirm_password_reset(db: Db, data: ResetConfirmIn) -> None:
    token_hash = hash_token(data.token)
    valid = {"token_hash": token_hash, "used_at": None, "expires_at": {"$gt": now()}}
    doc = await db.password_reset_tokens.find_one(valid)
    if doc is None:
        raise ValidationFailed(INVALID_RESET)
    reset = PasswordResetToken.from_doc(doc)
    user_doc = await db.users.find_one({"_id": reset.user_id})
    if user_doc is None:
        raise ValidationFailed(INVALID_RESET)
    user = User.from_doc(user_doc)
    # Validate before consuming, so a weak password doesn't burn the link.
    validate_password(data.password, user.email)
    # Atomic single use: only one request can flip used_at from null.
    consumed = await db.password_reset_tokens.find_one_and_update(
        valid, {"$set": {"used_at": now()}}, return_document=ReturnDocument.AFTER
    )
    if consumed is None:
        raise ValidationFailed(INVALID_RESET)
    await db.users.update_one(
        {"_id": user.id},
        {"$set": {"password_hash": hash_password(data.password), "updated_at": now()}},
    )
    await db.password_reset_tokens.update_many(
        {"user_id": user.id, "used_at": None}, {"$set": {"used_at": now()}}
    )
    await revoke_sessions(db, user.id)


async def change_password(db: Db, user: User, data: ChangePasswordIn, keep_token_hash: str) -> None:
    if not verify_password(data.current_password, user.password_hash):
        raise ValidationFailed("Current password is incorrect")
    validate_password(data.new_password, user.email)
    await db.users.update_one(
        {"_id": user.id},
        {"$set": {"password_hash": hash_password(data.new_password), "updated_at": now()}},
    )
    await revoke_sessions(db, user.id, except_token_hash=keep_token_hash)
