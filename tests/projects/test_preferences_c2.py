from app.modules.artifacts.models import Artifact
from app.modules.ratecard.service import seed_default
from tests.factories import login, make_user

BASE = "/api/v1/projects"


async def _setup(client, db):
    await seed_default(db)
    await make_user(db, email="ana@x.com")
    await login(client, "ana@x.com")
    pid = (await client.post(BASE, json={"name": "P"})).json()["id"]
    return pid


async def _put(client, pid, body):
    return await client.put(f"{BASE}/{pid}/preferences", json=body)


async def _plan(db, pid):
    import uuid

    p = await db.projects.find_one({"_id": uuid.UUID(pid)})
    art = Artifact(project_id=p["_id"], owner_id=p["owner_id"], kind="plan", latest_revision=1)
    await db.artifacts.insert_one(art.to_doc())
    return art


async def _stale(db, art):
    return (await db.artifacts.find_one({"_id": art.id}))["stale_reason"]


async def test_team_size_unknown_role_rejected(client, db):
    pid = await _setup(client, db)
    r = await _put(client, pid, {"team_size": {"astronaut": 2}})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "UNKNOWN_ROLE"
    assert "backend_engineer" in err["message"]


async def test_team_size_valid_and_start_date_saved(client, db):
    pid = await _setup(client, db)
    r = await _put(client, pid, {"team_size": {"backend_engineer": 2}, "start_date": "2026-11-02"})
    assert r.status_code == 200, r.text
    assert r.json()["preferences"]["team_size"] == {"backend_engineer": 2}


async def test_team_size_bounds(client, db):
    pid = await _setup(client, db)
    for n in (0, 21):
        r = await _put(client, pid, {"team_size": {"backend_engineer": n}})
        assert r.status_code == 422


async def test_changing_team_or_start_marks_plan_stale(client, db):
    pid = await _setup(client, db)
    art = await _plan(db, pid)
    await _put(client, pid, {"team_size": {"backend_engineer": 2}})
    assert await _stale(db, art) == "Team or start date changed"
    await db.artifacts.update_one({"_id": art.id}, {"$set": {"stale": False, "stale_reason": None}})
    await _put(client, pid, {"team_size": {"backend_engineer": 2}})
    assert await _stale(db, art) is None
    await _put(client, pid, {"team_size": {"backend_engineer": 2}, "start_date": "2026-11-02"})
    assert await _stale(db, art) == "Team or start date changed"


async def test_other_preference_change_does_not_touch_plan(client, db):
    pid = await _setup(client, db)
    art = await _plan(db, pid)
    await _put(client, pid, {"deployment_preference": "AWS"})
    assert await _stale(db, art) is None
