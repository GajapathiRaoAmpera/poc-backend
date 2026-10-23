import pytest

from app.core.config import Settings
from tests.factories import DEFAULT_PASSWORD, make_user
from tests.safety import assert_safe_test_database


def test_environment_defaults_to_production(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    s = Settings(_env_file=None, mongodb_uri="mongodb://x")
    assert s.environment == "production"
    assert s.secure_cookies is True


@pytest.mark.parametrize(
    ("uri", "name"),
    [
        ("mongodb+srv://u:p@cluster0.example.mongodb.net/", "ampera_test"),
        ("mongodb://localhost:27018/", "ampera"),
        ("mongodb://10.0.0.5:27018/", "ampera_test"),
    ],
)
def test_test_db_guard_rejects_non_local_or_non_test(uri, name):
    with pytest.raises(RuntimeError):
        assert_safe_test_database(uri, name)


def test_test_db_guard_accepts_local_test_db():
    assert_safe_test_database("mongodb://localhost:27018/?directConnection=true", "ampera_test")


async def _attempt_ip(db) -> str:
    doc = await db.login_attempts.find_one({})
    return doc["ip"]


async def test_forwarded_ip_used_only_from_trusted_proxy(monkeypatch, db):
    import httpx

    from app.core.config import get_settings
    from app.main import create_app

    await make_user(db, email="ana@x.com")
    body = {"email": "ana@x.com", "password": DEFAULT_PASSWORD}
    headers = {"Origin": "http://localhost:3000", "X-Forwarded-For": "203.0.113.9"}

    async def login_ip(trusted: str) -> str:
        monkeypatch.setenv("TRUSTED_PROXY_IPS", trusted)
        get_settings.cache_clear()
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("10.1.2.3", 5000)),
            base_url="http://test",
            headers=headers,
        ) as c:
            assert (await c.post("/api/v1/auth/login", json=body)).status_code == 200
        ip = await _attempt_ip(db)
        await db.login_attempts.delete_many({})
        return ip

    try:
        assert await login_ip("") == "10.1.2.3"
        assert await login_ip("10.1.2.3") == "203.0.113.9"
    finally:
        monkeypatch.delenv("TRUSTED_PROXY_IPS")
        get_settings.cache_clear()
