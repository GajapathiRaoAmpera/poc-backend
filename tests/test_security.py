import pytest

from app.core.errors import AppError
from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    validate_password,
    verify_password,
)


def test_hash_and_verify_roundtrip():
    h = hash_password("correct-horse-1")
    assert h.startswith("$argon2id$")
    assert verify_password("correct-horse-1", h)


def test_verify_wrong_password_returns_false():
    assert verify_password("nope-nope-nope", hash_password("correct-horse-1")) is False


def test_verify_garbage_hash_returns_false():
    assert verify_password("anything-long", "not-a-hash") is False


def test_hash_token_is_deterministic_sha256_hex():
    t = new_token()
    assert len(t) >= 43
    assert hash_token(t) == hash_token(t)
    assert len(hash_token(t)) == 64
    assert hash_token(t) != t
    assert new_token() != t


@pytest.mark.parametrize("password", ["a" * 9, "a" * 129])
def test_validate_password_rejects_bad_length(password):
    with pytest.raises(AppError) as exc:
        validate_password(password, "ana@x.com")
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.status == 422


def test_validate_password_rejects_email_any_case():
    with pytest.raises(AppError):
        validate_password("ANA@EXAMPLE.COM", "ana@example.com")


def test_validate_password_accepts_ten_chars():
    validate_password("a" * 10, "ana@x.com")
