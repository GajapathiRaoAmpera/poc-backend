import pytest

from app.modules.auth.models import UserRole
from tests.factories import login, make_user

BASE = "/api/v1/projects"


@pytest.fixture
async def two_users(client, make_client, db):
    await make_user(db, email="ana@x.com")
    await make_user(db, email="bob@x.com")
    await login(client, "ana@x.com")
    bob = make_client()
    await login(bob, "bob@x.com")
    project = (await client.post(BASE, json={"name": "Ana secret"})).json()
    await bob.post(BASE, json={"name": "Bob's"})
    return client, bob, project


async def test_other_user_gets_404_everywhere(two_users):
    ana, bob, p = two_users
    url = f"{BASE}/{p['id']}"
    responses = [
        await bob.get(url),
        await bob.patch(url, json={"name": "pwned"}),
        await bob.delete(url),
        await bob.get(f"{url}/specification"),
        await bob.put(f"{url}/specification", json={"basics": {"name": "pwned"}}),
    ]
    for r in responses:
        assert r.status_code == 404, r.text
        assert "Ana secret" not in r.text
    assert (await ana.get(url)).json()["name"] == "Ana secret"


async def test_list_excludes_other_users_projects(two_users):
    _, bob, _ = two_users
    names = [p["name"] for p in (await bob.get(BASE)).json()["items"]]
    assert names == ["Bob's"]


async def test_super_admin_sees_all(two_users, make_client, db):
    _, _, p = two_users
    await make_user(db, email="root@x.com", role=UserRole.SUPER_ADMIN)
    root = make_client()
    await login(root, "root@x.com")
    assert (await root.get(f"{BASE}/{p['id']}")).status_code == 200
    names = {x["name"] for x in (await root.get(BASE)).json()["items"]}
    assert names == {"Ana secret", "Bob's"}
