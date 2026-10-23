import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)

from app.modules.auth.models import UserRole, UserStatus


def _strip(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


Email = Annotated[EmailStr, BeforeValidator(_strip), AfterValidator(str.lower)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
# Length rules live in validate_password so the message is friendly; this caps the payload.
Password = Annotated[str, Field(max_length=1024)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterIn(_Strict):
    name: Name
    email: Email
    password: Password


class LoginIn(_Strict):
    email: Email
    password: Password


class ResetRequestIn(_Strict):
    email: Email


class ResetConfirmIn(_Strict):
    token: Annotated[str, Field(min_length=1, max_length=200)]
    password: Password


class ChangePasswordIn(_Strict):
    current_password: Password
    new_password: Password


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: UserRole
    status: UserStatus
    last_login_at: datetime | None
    created_at: datetime
