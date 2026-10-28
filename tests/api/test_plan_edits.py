from datetime import date

from app.modules.artifacts import service as artifacts
from tests.agents.test_code_agent import ready
from tests.factories import login, make_user

API = "/api/v1"


async def _setup(client, db):
    owner, project = await ready(db)  # plan revision 1 approved
    await login(client, owner.email)
    plan = (await artifacts.get_revision(db, project, "plan", 1)).content
    return project, plan, f"{API}/projects/{project.id}/plan/edits"


async def test_pin_and_effort_recompute_schedule(client, db):
    project, plan, url = await _setup(client, db)
    first = plan["tasks"][0]
    pin = date.fromisoformat(plan["schedule"]["end"]).isoformat()
    r = await client.post(
        url,
        json={
            "base_revision": 1,
            "edits": [{"task_id": first["id"], "not_before": pin, "effort_days": 3}],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["revision"] == 2 and body["origin"] == "user"
    task = next(t for t in body["content"]["tasks"] if t["id"] == first["id"])
    assert task["not_before"] == pin and task["effort_days"] == 3
    assert task["start"] >= pin
    assert body["content"]["schedule"]["end"] > plan["schedule"]["end"]
    assert body["summary"] == "Adjusted 1 task on the timeline"
    # null clears the pin; the task moves back
    r = await client.post(
        url, json={"base_revision": 2, "edits": [{"task_id": first["id"], "not_before": None}]}
    )
    task = next(t for t in r.json()["content"]["tasks"] if t["id"] == first["id"])
    assert (
        task["not_before"] is None and task["start"] == first["start"] and task["effort_days"] == 3
    )


async def test_conflict_unknown_task_and_bad_effort(client, db):
    project, plan, url = await _setup(client, db)
    tid = plan["tasks"][0]["id"]
    assert (
        await client.post(url, json={"base_revision": 0, "edits": [{"task_id": tid}]})
    ).status_code == 409
    r = await client.post(url, json={"base_revision": 1, "edits": [{"task_id": "T-999"}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_TASK"
    r = await client.post(
        url, json={"base_revision": 1, "edits": [{"task_id": tid, "effort_days": 0.3}]}
    )
    assert r.status_code == 422


async def test_other_user_gets_404(client, db, make_client):
    project, plan, url = await _setup(client, db)
    other = make_client()
    await make_user(db, email="other@x.com")
    await login(other, "other@x.com")
    r = await other.post(
        url, json={"base_revision": 1, "edits": [{"task_id": plan["tasks"][0]["id"]}]}
    )
    assert r.status_code == 404
