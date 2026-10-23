import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth import service
from tests.factories import login, make_user

NEW_PASSWORD = "brand-new-password-9"


@pytest.fixture
def issued_tokens(monkeypatch):
    """Capture raw tokens the service generates (the API never returns them)."""
    issued: list[str] = []
    original = service.new_token

    def _capture() -> str:
        t = original()
        issued.append(t)
        return t

    monkeypatch.setattr(service, "new_token", _capture)
    return issued


async def _request_reset(client, email: str):
    return await client.post("/api/v1/auth/password-reset/request", json={"email": email})


async def test_reset_request_always_202(client, db):
    await make_user(db, email="ana@x.com")
    assert (await _request_reset(client, "ana@x.com")).status_code == 202
    assert (await _request_reset(client, "nobody@x.com")).status_code == 202
    count = await db.password_reset_tokens.count_documents({})
    assert count == 1


async def test_reset_confirm_works_once_and_revokes_sessions(
    client, make_client, db, issued_tokens
):
    await make_user(db, email="ana@x.com")
    other = make_client()
    await login(other, "ana@x.com")
    issued_tokens.clear()

    await _request_reset(client, "ana@x.com")
    token = issued_tokens[-1]
    body = {"token": token, "password": NEW_PASSWORD}
    r = await client.post("/api/v1/auth/password-reset/confirm", json=body)
    assert r.status_code == 204

    assert (await other.get("/api/v1/auth/me")).status_code == 401
    await login(client, "ana@x.com", NEW_PASSWORD)

    again = await client.post("/api/v1/auth/password-reset/confirm", json=body)
    assert again.status_code == 422


async def test_reset_token_expires(client, db, issued_tokens):
    await make_user(db, email="ana@x.com")
    await _request_reset(client, "ana@x.com")
    await db.password_reset_tokens.update_many(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}}
    )
    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": issued_tokens[-1], "password": NEW_PASSWORD},
    )
    assert r.status_code == 422


async def test_reset_confirm_rejects_weak_password(client, db, issued_tokens):
    await make_user(db, email="ana@x.com")
    await _request_reset(client, "ana@x.com")
    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": issued_tokens[-1], "password": "short"},
    )
    assert r.status_code == 422


async def test_reset_link_not_logged_outside_development(client, db, caplog, issued_tokens):
    await make_user(db, email="ana@x.com")
    with caplog.at_level(logging.DEBUG):
        await _request_reset(client, "ana@x.com")
    assert issued_tokens[-1] not in caplog.text
