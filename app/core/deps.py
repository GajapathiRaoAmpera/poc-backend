from typing import Annotated

from fastapi import Depends, Request

from app.core.cookies import SESSION_COOKIE
from app.core.db import Db, get_db
from app.core.errors import Forbidden
from app.modules.auth.models import User, UserRole
from app.modules.auth.service import user_for_token

__all__ = [
    "SESSION_COOKIE",
    "CurrentUser",
    "DbSession",
    "SuperAdmin",
    "client_ip",
    "session_token",
]

DbSession = Annotated[Db, Depends(get_db)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def session_token(request: Request) -> str | None:
    """The cookie (web) or the `Authorization: Bearer` token (mobile app)."""
    if token := request.cookies.get(SESSION_COOKIE):
        return token
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return (token.strip() or None) if scheme.lower() == "bearer" else None


async def current_user(request: Request, db: DbSession) -> User:
    user, session = await user_for_token(db, session_token(request))
    request.state.session_token_hash = session.token_hash
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_super_admin(user: CurrentUser) -> User:
    if user.role != UserRole.SUPER_ADMIN:
        raise Forbidden("Only a Super Admin can do this")
    return user


SuperAdmin = Annotated[User, Depends(require_super_admin)]
