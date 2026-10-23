import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.errors import ValidationFailed

PASSWORD_MIN = 10
PASSWORD_MAX = 128

_hasher = PasswordHasher()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_password(pw: str, email: str) -> None:
    if len(pw) < PASSWORD_MIN:
        raise ValidationFailed(f"Password must be at least {PASSWORD_MIN} characters")
    if len(pw) > PASSWORD_MAX:
        raise ValidationFailed(f"Password must be at most {PASSWORD_MAX} characters")
    if pw.strip().lower() == email.strip().lower():
        raise ValidationFailed("Password must not be the same as your email")
