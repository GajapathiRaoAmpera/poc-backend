import asyncio

import pytest
from pymongo.errors import DuplicateKeyError

from app.core.db import INDEXES, ensure_indexes
from app.modules.auth.models import UserRole
from tests.factories import login, make_user


async def test_ensure_indexes_is_idempotent_and_complete(db):
    await ensure_indexes(db)
    await ensure_indexes(db)
    for name, models in INDEXES.items():
        info = await db[name].index_information()
        assert len(info) >= len(models) + 1, name  # + the default _id index
    assert (await db.users.index_information())["email_1"]["unique"] is True
    assert (await db.sessions.index_information())["expires_at_1"]["expireAfterSeconds"] == 0


async def test_email_unique_at_database_level(db):
    await make_user(db, email="ana@x.com")
    with pytest.raises(DuplicateKeyError):
        await make_user(db, email="ana@x.com")


async def test_concurrent_mutual_demotion_keeps_one_admin(make_client, db):
    a = await make_user(db, email="a@x.com", role=UserRole.SUPER_ADMIN)
    b = await make_user(db, email="b@x.com", role=UserRole.SUPER_ADMIN)
    ca, cb = make_client(), make_client()
    await login(ca, "a@x.com")
    await login(cb, "b@x.com")
    ra, rb = await asyncio.gather(
        ca.patch(f"/api/v1/admin/users/{b.id}", json={"role": "USER"}),
        cb.patch(f"/api/v1/admin/users/{a.id}", json={"role": "USER"}),
    )
    assert sorted([ra.status_code, rb.status_code]) == [200, 409]
    assert await db.users.count_documents({"role": "SUPER_ADMIN"}) == 1


async def test_admin_patch_with_nulls_is_not_a_500(client, db):
    await make_user(db, email="root@x.com", role=UserRole.SUPER_ADMIN)
    bob = await make_user(db, email="bob@x.com", name="Bob")
    await login(client, "root@x.com")
    r = await client.patch(
        f"/api/v1/admin/users/{bob.id}", json={"name": None, "role": None, "status": None}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Bob"


async def test_search_treats_regex_characters_literally(client, db):
    await make_user(db, email="a@x.com")
    await login(client, "a@x.com")
    for name in ["Plan (v1)", "Plan v1", "Alpha.*"]:
        await client.post("/api/v1/projects", json={"name": name})
    names = lambda r: sorted(p["name"] for p in r.json()["items"])  # noqa: E731
    assert names(await client.get("/api/v1/projects", params={"q": "(v1)"})) == ["Plan (v1)"]
    assert names(await client.get("/api/v1/projects", params={"q": ".*"})) == ["Alpha.*"]
