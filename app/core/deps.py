from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import Forbidden
from app.modules.auth.models import User, UserRole
from app.modules.auth.service import user_for_token

SESSION_COOKIE = "ampera_session"

DbSession = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def current_user(request: Request, db: DbSession) -> User:
    user, session = await user_for_token(db, request.cookies.get(SESSION_COOKIE))
    request.state.session_token_hash = session.token_hash
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_super_admin(user: CurrentUser) -> User:
    if user.role != UserRole.SUPER_ADMIN:
        raise Forbidden("Only a Super Admin can do this")
    return user


SuperAdmin = Annotated[User, Depends(require_super_admin)]
