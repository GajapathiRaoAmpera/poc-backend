from datetime import UTC, datetime, timedelta

from tests.factories import DEFAULT_PASSWORD, make_user

WRONG = {"email": "ana@x.com", "password": "wrong-password-1"}
RIGHT = {"email": "ana@x.com", "password": DEFAULT_PASSWORD}


async def test_sixth_failed_login_is_rate_limited(client, db):
    await make_user(db, email="ana@x.com")
    for _ in range(5):
        assert (await client.post("/api/v1/auth/login", json=WRONG)).status_code == 401
    r = await client.post("/api/v1/auth/login", json=RIGHT)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"


async def test_old_failures_do_not_count(client, db):
    await make_user(db, email="ana@x.com")
    for _ in range(5):
        await client.post("/api/v1/auth/login", json=WRONG)
    await db.login_attempts.update_many(
        {}, {"$set": {"created_at": datetime.now(UTC) - timedelta(minutes=16)}}
    )
    assert (await client.post("/api/v1/auth/login", json=RIGHT)).status_code == 200


async def test_throttle_is_case_insensitive_on_email(client, db):
    await make_user(db, email="ana@x.com")
    for i in range(5):
        email = "ANA@x.com" if i % 2 else "ana@x.com"
        await client.post("/api/v1/auth/login", json={**WRONG, "email": email})
    assert (await client.post("/api/v1/auth/login", json=RIGHT)).status_code == 429
