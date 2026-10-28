from app.modules.auth.models import UserRole
from tests.factories import DEFAULT_PASSWORD, make_user

MOBILE = {"X-Client": "mobile"}
CREDS = {"email": "ana@x.com", "password": DEFAULT_PASSWORD}


async def test_token_login_then_bearer_requests(make_client, db):
    await make_user(db, email="ana@x.com")
    app = make_client(origin=None)
    r = await app.post("/api/v1/auth/token", json=CREDS, headers=MOBILE)
    assert r.status_code == 200, r.text
    assert "set-cookie" not in r.headers
    body = r.json()
    assert body["user"]["email"] == "ana@x.com"
    auth = {**MOBILE, "Authorization": f"Bearer {body['token']}"}

    assert (await app.get("/api/v1/auth/me", headers=auth)).status_code == 200
    # A write without Origin passes the CSRF check for the mobile client.
    assert (await app.post("/api/v1/auth/logout", headers=auth)).status_code == 204
    assert (await app.get("/api/v1/auth/me", headers=auth)).status_code == 401


async def test_token_register(make_client):
    app = make_client(origin=None)
    r = await app.post("/api/v1/auth/token/register", json={**CREDS, "name": "Ana"}, headers=MOBILE)
    assert r.status_code == 201, r.text
    assert "set-cookie" not in r.headers
    auth = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await app.get("/api/v1/auth/me", headers=auth)).status_code == 200


async def test_super_admin_cannot_get_token(make_client, db):
    await make_user(db, email="ana@x.com", role=UserRole.SUPER_ADMIN)
    app = make_client(origin=None)
    r = await app.post("/api/v1/auth/token", json=CREDS, headers=MOBILE)
    assert r.status_code == 403
    assert await db.sessions.count_documents({}) == 0


async def test_mobile_header_with_cookie_still_needs_origin(make_client):
    app = make_client(origin=None)
    r = await app.post("/api/v1/auth/logout", headers={**MOBILE, "Cookie": "ampera_session=x"})
    assert r.status_code == 403


async def test_bad_bearer_is_unauthenticated(make_client):
    app = make_client(origin=None)
    for value in ("Bearer nope", "Bearer ", "Basic abc"):
        r = await app.get("/api/v1/auth/me", headers={"Authorization": value})
        assert r.status_code == 401
