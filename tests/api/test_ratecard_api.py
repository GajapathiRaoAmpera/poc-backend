import uuid

from app.modules.ratecard.service import seed_default
from tests.factories import login, make_user

API = "/api/v1"
BODY = {
    "currency": "USD",
    "hours_per_day": "8",
    "roles": [{"role": "backend_engineer", "hourly_rate": "70"}],
}


async def test_non_admin_cannot_put_default(owner_client, db):
    await seed_default(db)
    assert (await owner_client.put(f"{API}/admin/rate-card", json=BODY)).status_code == 403


async def test_admin_roundtrip_and_invalid_body(client, db):
    await seed_default(db)
    from app.modules.auth.models import UserRole

    await make_user(db, email="root@x.com", role=UserRole.SUPER_ADMIN)
    await login(client, "root@x.com")
    got = (await client.get(f"{API}/admin/rate-card")).json()
    assert got["placeholder"] is True
    assert (await client.put(f"{API}/admin/rate-card", json=BODY)).status_code == 200
    got = (await client.get(f"{API}/admin/rate-card")).json()
    assert got["placeholder"] is False and got["roles"][0]["hourly_rate"] == "70"
    assert (
        await client.put(f"{API}/admin/rate-card", json={**BODY, "hours_per_day": "13"})
    ).status_code == 422


async def test_project_rate_card(owner_client, project_id, db, make_client):
    await seed_default(db)
    url = f"{API}/projects/{project_id}/rate-card"
    got = (await owner_client.get(url)).json()
    assert got["source"] == "platform" and got["override"] is None and got["placeholder"] is True
    r = await owner_client.put(url, json=BODY)
    assert r.status_code == 200 and r.json()["source"] == "project"
    assert r.json()["effective"]["roles"][0]["hourly_rate"] == "70"
    assert (
        await owner_client.put(url, content="null", headers={"content-type": "application/json"})
    ).json()["source"] == "platform"
    assert (await owner_client.put(url, json={**BODY, "currency": "usd"})).status_code == 422

    other = make_client()
    await make_user(db, email="other@x.com")
    await login(other, "other@x.com")
    assert (await other.get(url)).status_code == 404
    assert (await owner_client.get(f"{API}/projects/{uuid.uuid4()}/rate-card")).status_code == 404
