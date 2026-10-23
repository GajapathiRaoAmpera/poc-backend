import uuid

from tests.factories import login, make_user

BASE = "/api/v1/projects"


async def _signed_in(client, db, email="ana@x.com"):
    user = await make_user(db, email=email)
    await login(client, email)
    return user


async def test_crud_happy_path(client, db):
    ana = await _signed_in(client, db)
    r = await client.post(BASE, json={"name": "  Portal  ", "description": "Client portal"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["name"] == "Portal"
    assert p["status"] == "DRAFT"
    assert p["completeness"] == 0.0
    assert p["owner_id"] == str(ana.id)

    r = await client.patch(f"{BASE}/{p['id']}", json={"name": "Portal v2", "status": "ACTIVE"})
    assert r.status_code == 200
    assert r.json()["name"] == "Portal v2"
    assert r.json()["status"] == "ACTIVE"

    assert (await client.get(f"{BASE}/{p['id']}")).json()["name"] == "Portal v2"
    assert (await client.delete(f"{BASE}/{p['id']}")).status_code == 204
    assert (await client.get(f"{BASE}/{p['id']}")).status_code == 404


async def test_name_validation(client, db):
    await _signed_in(client, db)
    assert (await client.post(BASE, json={"name": "   "})).status_code == 422
    assert (await client.post(BASE, json={"name": "x" * 161})).status_code == 422
    assert (await client.post(BASE, json={"name": "x" * 160})).status_code == 201


async def test_list_sorted_search_filter(client, db):
    await _signed_in(client, db)
    ids = []
    for name in ["Alpha", "Beta", "Gamma"]:
        ids.append((await client.post(BASE, json={"name": name})).json()["id"])
    await client.patch(f"{BASE}/{ids[0]}", json={"description": "touched"})

    body = (await client.get(BASE)).json()
    assert [p["name"] for p in body["items"]] == ["Alpha", "Gamma", "Beta"]
    assert body["total"] == 3

    assert [p["name"] for p in (await client.get(BASE, params={"q": "amm"})).json()["items"]] == [
        "Gamma"
    ]
    await client.patch(f"{BASE}/{ids[1]}", json={"status": "ARCHIVED"})
    archived = (await client.get(BASE, params={"status": "ARCHIVED"})).json()["items"]
    assert [p["name"] for p in archived] == ["Beta"]


async def test_random_uuid_and_malformed_id(client, db):
    await _signed_in(client, db)
    assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404
    assert (await client.get(f"{BASE}/abc")).status_code == 422


async def test_requires_login(client):
    assert (await client.get(BASE)).status_code == 401
