"""Fernet encryption for secrets stored in Mongo (repo tokens)."""

from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.core.errors import AppError


def _fernet() -> Fernet:
    key = get_settings().secret_box_key
    if key is None:
        raise AppError("SECRET_BOX_UNCONFIGURED", "SECRET_BOX_KEY is not configured", 503)
    return Fernet(key.get_secret_value().encode())


def seal(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def open_(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
