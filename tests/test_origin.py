from tests.factories import DEFAULT_PASSWORD

BODY = {"name": "Ana", "email": "ana@x.com", "password": DEFAULT_PASSWORD}


async def test_cross_origin_post_rejected(make_client):
    evil = make_client(origin="https://evil.example")
    r = await evil.post("/api/v1/auth/register", json=BODY)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_missing_origin_post_rejected(make_client):
    bare = make_client(origin=None)
    r = await bare.post("/api/v1/auth/register", json=BODY)
    assert r.status_code == 403


async def test_get_without_origin_allowed(make_client):
    bare = make_client(origin=None)
    assert (await bare.get("/api/v1/health")).status_code == 200
