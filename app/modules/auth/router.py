from fastapi import APIRouter, Request, Response, status

from app.core.config import get_settings
from app.core.deps import SESSION_COOKIE, CurrentUser, DbSession, client_ip, session_token
from app.core.errors import Forbidden
from app.modules.auth import service
from app.modules.auth.models import User, UserRole
from app.modules.auth.schemas import (
    ChangePasswordIn,
    LoginIn,
    RegisterIn,
    ResetConfirmIn,
    ResetRequestIn,
    TokenOut,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(service.SESSION_TTL.total_seconds()),
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite="lax",
        path="/",
    )


async def _start_session(db: DbSession, user: User, request: Request, response: Response) -> None:
    token = await service.create_session(
        db, user, client_ip(request), request.headers.get("user-agent")
    )
    _set_session_cookie(response, token)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def register(data: RegisterIn, request: Request, response: Response, db: DbSession) -> User:
    user = await service.register(db, data)
    await _start_session(db, user, request, response)
    return user


@router.post("/login", response_model=UserOut)
async def login(data: LoginIn, request: Request, response: Response, db: DbSession) -> User:
    user = await service.authenticate(db, data, client_ip(request))
    await _start_session(db, user, request, response)
    return user


async def _token_out(db: DbSession, user: User, request: Request) -> TokenOut:
    # The mobile app is for regular users; admin tools stay on the web.
    if user.role == UserRole.SUPER_ADMIN:
        raise Forbidden("Admin accounts sign in on the web app")
    token = await service.create_session(
        db, user, client_ip(request), request.headers.get("user-agent")
    )
    return TokenOut(token=token, user=UserOut.model_validate(user))


@router.post("/token", response_model=TokenOut)
async def login_token(data: LoginIn, request: Request, db: DbSession) -> TokenOut:
    """Mobile sign-in: the session token comes back in the body, no cookie is set."""
    user = await service.authenticate(db, data, client_ip(request))
    return await _token_out(db, user, request)


@router.post("/token/register", status_code=status.HTTP_201_CREATED, response_model=TokenOut)
async def register_token(data: RegisterIn, request: Request, db: DbSession) -> TokenOut:
    user = await service.register(db, data)
    return await _token_out(db, user, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, db: DbSession) -> Response:
    token = session_token(request)
    if token:
        await service.revoke_session(db, token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def password_reset_request(data: ResetRequestIn, db: DbSession) -> dict[str, str]:
    await service.request_password_reset(db, data.email)
    return {"status": "accepted"}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_confirm(data: ResetConfirmIn, db: DbSession) -> None:
    await service.confirm_password_reset(db, data)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: ChangePasswordIn, request: Request, user: CurrentUser, db: DbSession
) -> None:
    await service.change_password(db, user, data, request.state.session_token_hash)
