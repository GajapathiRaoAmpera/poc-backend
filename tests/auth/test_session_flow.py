from datetime import UTC, datetime, timedelta

from app.core.security import hash_token
from app.modules.auth.models import UserStatus
from tests.factories import DEFAULT_PASSWORD, login, make_user

REGISTER = {"name": "Ana", "email": " Ana@X.com ", "password": DEFAULT_PASSWORD}


async def test_register_signs_in_and_me_returns_user(client):
    r = await client.post("/api/v1/auth/register", json=REGISTER)
    assert r.status_code == 201, r.text
    set_cookie = r.headers["set-cookie"]
    assert "ampera_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "ana@x.com"
    assert body["role"] == "USER"
    assert body["status"] == "ACTIVE"
    assert "password_hash" not in body


async def test_register_duplicate_email_case_insensitive(client, make_client):
    await client.post("/api/v1/auth/register", json=REGISTER)
    other = make_client()
    r = await other.post("/api/v1/auth/register", json={**REGISTER, "email": "ANA@x.com"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


async def test_register_weak_password(client):
    r = await client.post("/api/v1/auth/register", json={**REGISTER, "password": "short"})
    assert r.status_code == 422


async def test_register_rejects_blank_name(client):
    r = await client.post("/api/v1/auth/register", json={**REGISTER, "name": "   "})
    assert r.status_code == 422


async def test_login_logout(client, db):
    await make_user(db, email="ana@x.com")
    await login(client, " ANA@x.com ")
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_login_wrong_password_and_unknown_email_same_message(client, db):
    await make_user(db, email="ana@x.com")
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "ana@x.com", "password": "wrong-password-1"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@x.com", "password": "wrong-password-1"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_disabled_user_cannot_login(client, db):
    await make_user(db, email="ana@x.com", status=UserStatus.DISABLED)
    r = await client.post(
        "/api/v1/auth/login", json={"email": "ana@x.com", "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 401


async def test_me_without_cookie_is_401(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_expired_session_rejected(client, db):
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    await db.sessions.update_many(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_session_sliding_expiry(client, db):
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    now = datetime.now(UTC)

    # Extended 10 minutes ago: left alone.
    recent = now + timedelta(days=7) - timedelta(minutes=10)
    await db.sessions.update_many({}, {"$set": {"expires_at": recent}})
    await client.get("/api/v1/auth/me")
    s = await db.sessions.find_one({})
    assert abs((s["expires_at"] - recent).total_seconds()) < 1

    # Extended 2 hours ago: pushed to now + 7 days.
    stale = now + timedelta(days=7) - timedelta(hours=2)
    await db.sessions.update_many({}, {"$set": {"expires_at": stale}})
    await client.get("/api/v1/auth/me")
    s = await db.sessions.find_one({})
    assert s["expires_at"] > now + timedelta(days=7) - timedelta(minutes=1)


async def test_session_db_stores_hash_only(client, db):
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    raw = client.cookies.get("ampera_session")
    s = await db.sessions.find_one({})
    assert s["token_hash"] != raw
    assert s["token_hash"] == hash_token(raw)


async def test_login_updates_last_login(client, db):
    user = await make_user(db, email="ana@x.com")
    assert user.last_login_at is None
    await login(client, "ana@x.com")
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["last_login_at"] is not None


async def test_rejected_session_cookie_is_cleared(client, db):
    """A stale cookie must be removed, or the web app's redirect guard loops forever."""
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    await db.sessions.update_many(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    r = await client.get("/api/v1/projects")
    assert r.status_code == 401
    set_cookie = r.headers.get("set-cookie", "")
    assert "ampera_session=" in set_cookie
    assert "max-age=0" in set_cookie.lower() or "expires=thu, 01 jan 1970" in set_cookie.lower()


async def test_401_without_cookie_sets_no_cookie(client):
    r = await client.get("/api/v1/projects")
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
