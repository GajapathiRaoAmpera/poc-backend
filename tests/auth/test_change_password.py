from tests.factories import DEFAULT_PASSWORD, login, make_user

NEW = "brand-new-password-9"


async def test_change_password_revokes_other_sessions(client, make_client, db):
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    other = make_client()
    await login(other, "ana@x.com")

    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": NEW},
    )
    assert r.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await other.get("/api/v1/auth/me")).status_code == 401
    await login(make_client(), "ana@x.com", NEW)


async def test_change_password_wrong_current_is_422(client, db):
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "not-my-password", "new_password": NEW},
    )
    assert r.status_code == 422
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_change_password_requires_login(client):
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": NEW},
    )
    assert r.status_code == 401
