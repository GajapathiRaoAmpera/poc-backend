import uuid

import pytest
from sqlalchemy import func, select

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User, UserRole, UserStatus
from app.modules.projects.models import Project
from tests.factories import login, make_user

BASE = "/api/v1/admin/users"


@pytest.fixture
async def admin_client(client, db):
    await make_user(db, email="root@x.com", name="Root", role=UserRole.SUPER_ADMIN)
    await login(client, "root@x.com")
    return client


async def test_non_admin_forbidden_everywhere(client, db):
    target = await make_user(db, email="bob@x.com")
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    calls = [
        client.get(BASE),
        client.post(BASE, json={"name": "X", "email": "x@x.com", "password": "abcdefghijk"}),
        client.get(f"{BASE}/{target.id}"),
        client.patch(f"{BASE}/{target.id}", json={"name": "Y"}),
        client.delete(f"{BASE}/{target.id}"),
    ]
    for call in calls:
        r = await call
        assert r.status_code == 403, r.text


async def test_anonymous_gets_401(client):
    assert (await client.get(BASE)).status_code == 401


async def test_list_search_filter_paginate(admin_client, db):
    for i in range(24):
        await make_user(db, email=f"user{i:02d}@x.com", name=f"User {i:02d}")
    r = await admin_client.get(BASE, params={"page_size": 10, "page": 3})
    body = r.json()
    assert body["total"] == 25
    assert len(body["items"]) == 5
    assert body["page"] == 3

    await make_user(db, email="zed@x.com", name="Anabel")
    found = (await admin_client.get(BASE, params={"q": "anab"})).json()
    assert [u["email"] for u in found["items"]] == ["zed@x.com"]
    by_mail = (await admin_client.get(BASE, params={"q": "USER07@"})).json()
    assert by_mail["total"] == 1

    admins = (await admin_client.get(BASE, params={"role": "SUPER_ADMIN"})).json()
    assert [u["email"] for u in admins["items"]] == ["root@x.com"]

    assert (await admin_client.get(BASE, params={"page_size": 101})).status_code == 422
    assert (await admin_client.get(BASE, params={"page": 0})).status_code == 422


async def test_create_user_and_duplicate(admin_client):
    body = {"name": "Bob", "email": "Bob@x.com", "password": "abcdefghijk", "role": "USER"}
    r = await admin_client.post(BASE, json=body)
    assert r.status_code == 201
    assert r.json()["email"] == "bob@x.com"
    assert r.json()["daily_run_limit"] is None
    assert (await admin_client.post(BASE, json=body)).status_code == 409


async def test_get_unknown_user_404(admin_client):
    assert (await admin_client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404


async def test_update_fields(admin_client, db):
    bob = await make_user(db, email="bob@x.com")
    r = await admin_client.patch(
        f"{BASE}/{bob.id}", json={"name": "Robert", "role": "SUPER_ADMIN", "daily_run_limit": 5}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Robert"
    assert r.json()["role"] == "SUPER_ADMIN"
    assert r.json()["daily_run_limit"] == 5
    bad = await admin_client.patch(f"{BASE}/{bob.id}", json={"daily_run_limit": -1})
    assert bad.status_code == 422


async def test_disable_user_revokes_sessions(admin_client, make_client, db):
    bob = await make_user(db, email="bob@x.com")
    bob_client = make_client()
    await login(bob_client, "bob@x.com")
    r = await admin_client.patch(f"{BASE}/{bob.id}", json={"status": "DISABLED"})
    assert r.status_code == 200
    assert (await bob_client.get("/api/v1/auth/me")).status_code == 401


async def test_delete_user_cascades_projects(admin_client, make_client, db):
    bob = await make_user(db, email="bob@x.com")
    db.add(Project(owner_id=bob.id, name="Bob's"))
    await db.commit()
    bob_client = make_client()
    await login(bob_client, "bob@x.com")

    assert (await admin_client.delete(f"{BASE}/{bob.id}")).status_code == 204
    assert (await db.execute(select(func.count()).select_from(Project))).scalar() == 0
    assert (await bob_client.get("/api/v1/auth/me")).status_code == 401


async def test_audit_rows_written(admin_client, db):
    r = await admin_client.post(
        BASE, json={"name": "Bob", "email": "bob@x.com", "password": "abcdefghijk"}
    )
    bob_id = r.json()["id"]
    await admin_client.patch(f"{BASE}/{bob_id}", json={"role": "SUPER_ADMIN"})
    await admin_client.delete(f"{BASE}/{bob_id}")

    rows = (await db.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars().all()
    assert [r.action for r in rows] == ["user.created", "user.updated", "user.deleted"]
    assert rows[1].metadata_["changes"]["role"] == {"from": "USER", "to": "SUPER_ADMIN"}
    root = (await db.execute(select(User).where(User.email == "root@x.com"))).scalar_one()
    assert all(r.actor_id == root.id for r in rows)
    assert all("password" not in str(r.metadata_) for r in rows)


async def test_admin_sees_disabled_status_filter(admin_client, db):
    await make_user(db, email="off@x.com", status=UserStatus.DISABLED)
    r = (await admin_client.get(BASE, params={"status": "DISABLED"})).json()
    assert [u["email"] for u in r["items"]] == ["off@x.com"]
