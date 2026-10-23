import asyncio

import pytest
from sqlalchemy import select

from app.modules.projects.models import ProjectSpecification
from tests.factories import login, make_user

FULL = {
    "basics": {"name": "Portal", "description": "Client portal"},
    "problem": {
        "business_problem": "Manual onboarding",
        "target_users": ["Clients"],
        "goals": ["Faster onboarding"],
        "expected_outcomes": [],
    },
    "requirements": {"functional": ["Login"], "non_functional": [], "expected_scale": "1k users"},
    "constraints": {
        "preferred_technologies": ["Python"],
        "deployment_preference": "AWS",
        "budget": {"min": 1000, "max": 5000, "currency": "USD"},
        "target_delivery_date": "2027-03-31",
        "team_assumptions": "2 devs",
        "compliance": ["GDPR"],
        "existing_integrations": [],
    },
}


@pytest.fixture
async def project(client, db):
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    return (await client.post("/api/v1/projects", json={"name": "Draft"})).json()


def _url(project) -> str:
    return f"/api/v1/projects/{project['id']}/specification"


async def test_get_empty_spec(client, project):
    r = await client.get(_url(project))
    assert r.status_code == 200
    assert r.json()["revision"] == 0
    assert r.json()["data"] == {}


async def test_revisions_append_only(client, db, project):
    r1 = await client.put(_url(project), json={"basics": {"name": "  First  "}})
    assert r1.status_code == 201, r1.text
    assert r1.json()["revision"] == 1
    assert r1.json()["data"]["basics"]["name"] == "First"

    r2 = await client.put(_url(project), json=FULL)
    assert r2.json()["revision"] == 2

    latest = (await client.get(_url(project))).json()
    assert latest["revision"] == 2
    assert latest["data"]["constraints"]["budget"]["currency"] == "USD"

    rows = (
        (await db.execute(select(ProjectSpecification).order_by(ProjectSpecification.revision)))
        .scalars()
        .all()
    )
    assert [r.revision for r in rows] == [1, 2]
    assert rows[0].data == {"basics": {"name": "First"}}


async def test_basics_name_renames_project_and_completeness(client, project):
    await client.put(_url(project), json=FULL)
    p = (await client.get(f"/api/v1/projects/{project['id']}")).json()
    assert p["name"] == "Portal"
    assert p["completeness"] == 1.0

    await client.put(_url(project), json={"basics": {"name": "Portal"}, "problem": {"goals": []}})
    p = (await client.get(f"/api/v1/projects/{project['id']}")).json()
    assert p["completeness"] == 0.25


@pytest.mark.parametrize(
    "body",
    [
        {"unknown": {}},
        {"basics": {"name": "x", "extra": 1}},
        {"basics": {"description": "x" * 5001}},
        {"problem": {"target_users": ["x" * 301]}},
        {"problem": {"goals": ["g"] * 51}},
        {"constraints": {"budget": {"currency": "usd"}}},
        {"constraints": {"budget": {"min": 10, "max": 5, "currency": "USD"}}},
        {"constraints": {"budget": {"min": -1}}},
        {"constraints": {"target_delivery_date": "not-a-date"}},
    ],
)
async def test_invalid_spec_rejected(client, project, body):
    r = await client.put(_url(project), json=body)
    assert r.status_code == 422, body


async def test_long_requirement_item_allowed(client, project):
    r = await client.put(_url(project), json={"requirements": {"functional": ["x" * 4000]}})
    assert r.status_code == 201


async def test_concurrent_puts(client, db, project):
    results = await asyncio.gather(
        client.put(_url(project), json={"basics": {"description": "a"}}),
        client.put(_url(project), json={"basics": {"description": "b"}}),
        client.put(_url(project), json={"basics": {"description": "c"}}),
    )
    codes = [r.status_code for r in results]
    assert set(codes) <= {201, 409}, codes
    revisions = (await db.execute(select(ProjectSpecification.revision))).scalars().all()
    assert len(revisions) == len(set(revisions)) == codes.count(201)
