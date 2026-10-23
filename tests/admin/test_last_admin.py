from app.modules.auth.models import User, UserRole
from tests.factories import login, make_user

BASE = "/api/v1/admin/users"


async def _root(client, db) -> User:
    root = await make_user(db, email="root@x.com", role=UserRole.SUPER_ADMIN)
    await login(client, "root@x.com")
    return root


async def test_last_admin_cannot_be_demoted_disabled_or_deleted(client, db):
    root = await _root(client, db)
    for body in ({"role": "USER"}, {"status": "DISABLED"}):
        r = await client.patch(f"{BASE}/{root.id}", json=body)
        assert r.status_code == 409, body
        assert "last" in r.json()["error"]["message"].lower()
    assert (await client.delete(f"{BASE}/{root.id}")).status_code == 409
    role = (await db.users.find_one({"_id": root.id}))["role"]
    assert role == UserRole.SUPER_ADMIN


async def test_demotion_allowed_with_two_admins(client, db):
    await _root(client, db)
    other = await make_user(db, email="ops@x.com", role=UserRole.SUPER_ADMIN)
    r = await client.patch(f"{BASE}/{other.id}", json={"role": "USER"})
    assert r.status_code == 200


async def test_admin_cannot_delete_self(client, db):
    root = await _root(client, db)
    await make_user(db, email="ops@x.com", role=UserRole.SUPER_ADMIN)
    r = await client.delete(f"{BASE}/{root.id}")
    assert r.status_code == 409
