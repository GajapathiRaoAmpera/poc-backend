from pydantic import BaseModel


async def test_health_ok(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_unknown_route_uses_error_format(client):
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


class _Body(BaseModel):
    name: str


async def test_validation_error_format(app, client):
    @app.post("/api/v1/_test/echo")
    async def echo(body: _Body):
        return body

    r = await client.post("/api/v1/_test/echo", json={})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert isinstance(err["details"], list)


async def test_unhandled_error_is_generic_500(app):
    import httpx

    @app.get("/api/v1/_test/boom")
    async def boom():
        raise RuntimeError("secret internals")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/_test/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL"
    assert "secret" not in r.text
