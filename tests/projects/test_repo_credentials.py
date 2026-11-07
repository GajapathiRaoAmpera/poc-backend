import uuid

import pytest
from cryptography.fernet import Fernet

from app.core import secret_box
from app.core.config import get_settings
from app.core.errors import AppError
from tests.factories import login, make_user

BASE = "/api/v1/projects"
TOKEN = "ghp_supersecret1234abcd"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("SECRET_BOX_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _setup(client, db, email="ana@x.com"):
    await make_user(db, email=email)
    await login(client, email)
    return (await client.post(BASE, json={"name": "P"})).json()["id"]


async def test_put_get_repo_never_returns_token(client, db):
    pid = await _setup(client, db)
    url = "https://github.com/acme/app.git"
    put = await client.put(f"{BASE}/{pid}/repo", json={"url": url, "token": TOKEN})
    got = await client.get(f"{BASE}/{pid}/repo")
    for r in (put, got):
        assert r.status_code == 200
        assert r.json() == {"url": url, "token_set": True, "token_last4": TOKEN[-4:]}
        assert TOKEN not in r.text
    # None token keeps the existing one
    r = await client.put(f"{BASE}/{pid}/repo", json={"url": url, "token": None})
    assert r.json()["token_last4"] == TOKEN[-4:]


async def test_token_encrypted_at_rest(client, db):
    pid = await _setup(client, db)
    await client.put(f"{BASE}/{pid}/repo", json={"url": "https://x.io/a/b", "token": TOKEN})
    doc = await db.projects.find_one({"_id": uuid.UUID(pid)})
    assert doc["repo_token_enc"] != TOKEN and TOKEN not in str(doc)
    assert secret_box.open_(doc["repo_token_enc"]) == TOKEN


@pytest.mark.parametrize(
    "url,code",
    [
        ("http://x/y", 422),
        ("git@github.com:a/b.git", 422),
        ("https://x/../y", 422),
        ("https://github.com/acme/app.git", 200),
    ],
)
async def test_repo_url_validation(client, db, url, code):
    pid = await _setup(client, db)
    assert (await client.put(f"{BASE}/{pid}/repo", json={"url": url})).status_code == code


async def test_other_user_404(client, db):
    pid = await _setup(client, db)
    await make_user(db, email="bob@x.com")
    await login(client, "bob@x.com")
    body = {"url": "https://x.io/a/b", "token": TOKEN}
    assert (await client.put(f"{BASE}/{pid}/repo", json=body)).status_code == 404
    assert (await client.get(f"{BASE}/{pid}/repo")).status_code == 404


async def test_unconfigured_key_raises(monkeypatch):
    monkeypatch.delenv("SECRET_BOX_KEY")
    get_settings.cache_clear()
    with pytest.raises(AppError) as e:
        secret_box.seal("x")
    assert e.value.code == "SECRET_BOX_UNCONFIGURED"
