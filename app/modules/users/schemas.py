from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.modules.auth.models import UserRole, UserStatus
from app.modules.auth.schemas import Email, Name, Password, UserOut, _Strict


class AdminUserOut(UserOut):
    daily_run_limit: int | None
    updated_at: datetime


class AdminUserCreate(_Strict):
    name: Name
    email: Email
    password: Password
    role: UserRole = UserRole.USER


class AdminUserUpdate(_Strict):
    name: Name | None = None
    role: UserRole | None = None
    status: UserStatus | None = None
    daily_run_limit: Annotated[int, Field(ge=0)] | None = None
