import copy

import pytest

from app.core.errors import AppError
from app.modules.artifacts import service as artifacts
from app.modules.changesets import service as changesets
from tests.agents.helpers import approved_requirements, good_architecture, run_stage, setup_project

REDIS_OPS = {
    "ops": [
        {
            "op": "add_node",
            "node": {
                "id": "n_redis",
                "type": "cache",
                "label": "Redis",
                "description": "",
                "technology": "Redis 7",
                "responsibilities": [],
                "config": {},
                "requirement_ids": [],
                "position": {"x": 0, "y": 0},
                "group": None,
            },
        },
        {"op": "remove_edge", "id": "e_api_worker"},
        {
            "op": "add_edge",
            "edge": {
                "id": "e_api_redis",
                "source": "n_api",
                "target": "n_redis",
                "label": "enqueue",
                "protocol": None,
                "data": None,
                "requirement_ids": [],
            },
        },
        {
            "op": "add_edge",
            "edge": {
                "id": "e_redis_worker",
                "source": "n_redis",
                "target": "n_worker",
                "label": "consume",
                "protocol": None,
                "data": None,
                "requirement_ids": [],
            },
        },
    ],
    "summary": "Inserted Redis between the API and the worker.",
}


async def _with_architecture(db):
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    await run_stage(db, project, owner, "architecture", lambda *a: good_architecture())
    return owner, project


async def _request(
    db, project, owner, script, instruction="Add Redis between the API and the worker"
):
    run, provider, doc = await run_stage(
        db,
        project,
        owner,
        "architecture",
        script,
        mode="change_request",
        instruction=instruction,
        base_revision=1,
    )
    return run, provider, doc


async def test_change_request_creates_proposed_change_set_without_new_revision(db):
    owner, project = await _with_architecture(db)
    _, provider, doc = await _request(db, project, owner, lambda *a: copy.deepcopy(REDIS_OPS))
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    cs = await changesets.get_change_set(db, project, doc["output"]["change_set_id"])
    assert cs.status == "PROPOSED" and cs.base_revision == 1
    assert cs.impact.added == ["n_redis", "e_api_redis", "e_redis_worker"]
    assert cs.impact.removed == ["e_api_worker"]
    assert "Add Redis between the API and the worker" in provider.calls[0]["user"]
    assert "<current_architecture>" in provider.calls[0]["user"]
    assert (await artifacts.get_artifact(db, project, "architecture")).latest_revision == 1


async def test_invalid_ops_are_repaired(db):
    owner, project = await _with_architecture(db)
    bad = {"ops": [{"op": "remove_node", "id": "n_cache"}], "summary": "x"}
    outputs = [bad, copy.deepcopy(REDIS_OPS)]
    _, provider, doc = await _request(db, project, owner, lambda *a: outputs.pop(0))
    assert doc["status"] == "SUCCEEDED"
    assert "n_cache" in provider.calls[1]["user"]


async def test_invalid_ops_never_applied(db):
    owner, project = await _with_architecture(db)
    bad = {"ops": [{"op": "remove_node", "id": "n_cache"}], "summary": "x"}
    _, _, doc = await _request(db, project, owner, lambda *a: copy.deepcopy(bad))
    assert doc["status"] == "FAILED"
    assert await db.change_sets.count_documents({}) == 0
    assert (await artifacts.get_artifact(db, project, "architecture")).latest_revision == 1


async def test_apply_creates_revision(db):
    owner, project = await _with_architecture(db)
    _, _, doc = await _request(db, project, owner, lambda *a: copy.deepcopy(REDIS_OPS))
    rev = await changesets.apply_change_set(db, project, doc["output"]["change_set_id"], owner)
    assert rev.revision == 2 and rev.origin == "change_set"
    assert "n_redis" in {n["id"] for n in rev.content["nodes"]}
    cs = await changesets.get_change_set(db, project, doc["output"]["change_set_id"])
    assert cs.status == "APPLIED" and cs.applied_revision == 2
    with pytest.raises(AppError):
        await changesets.apply_change_set(db, project, cs.id, owner)


async def test_apply_after_manual_save_is_superseded(db):
    owner, project = await _with_architecture(db)
    _, _, doc = await _request(db, project, owner, lambda *a: copy.deepcopy(REDIS_OPS))
    base = await artifacts.get_revision(db, project, "architecture", 1)
    manual = copy.deepcopy(base.content)
    manual["nodes"][0]["label"] = "Customer portal"
    await artifacts.add_revision(
        db,
        project,
        "architecture",
        manual,
        sources=base.sources,
        origin="user",
        summary="Renamed web app",
        created_by=owner.id,
        base_revision=1,
    )
    with pytest.raises(AppError) as e:
        await changesets.apply_change_set(db, project, doc["output"]["change_set_id"], owner)
    assert e.value.code == "REVISION_CONFLICT"
    cs = await changesets.get_change_set(db, project, doc["output"]["change_set_id"])
    assert cs.status == "SUPERSEDED"
    latest = await artifacts.get_revision(db, project, "architecture", 2)
    assert latest.content["nodes"][0]["label"] == "Customer portal"
    assert "n_redis" not in {n["id"] for n in latest.content["nodes"]}


async def test_reject_creates_no_revision(db):
    owner, project = await _with_architecture(db)
    _, _, doc = await _request(db, project, owner, lambda *a: copy.deepcopy(REDIS_OPS))
    cs = await changesets.reject_change_set(db, project, doc["output"]["change_set_id"], owner)
    assert cs.status == "REJECTED"
    assert (await artifacts.get_artifact(db, project, "architecture")).latest_revision == 1


async def test_change_request_without_architecture_is_blocked(db):
    from app.modules.orchestration.service import start_stage

    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    with pytest.raises(AppError) as e:
        await start_stage(db, project, "architecture", "change_request", owner, instruction="x")
    assert e.value.code == "STAGE_BLOCKED"


async def test_mock_default_change_set_is_valid(db):
    from app.llm.mock import MockProvider
    from app.modules.orchestration.service import start_stage
    from app.worker.runner import Worker

    owner, project = await _with_architecture(db)
    await start_stage(
        db,
        project,
        "architecture",
        "change_request",
        owner,
        instruction="Add Redis between the API and the worker",
        base_revision=1,
    )
    await Worker(db, provider_factory=MockProvider, heartbeat_s=0.05).run_once()
    cs = await db.change_sets.find_one({})
    assert cs["status"] == "PROPOSED" and any("redis" in a for a in cs["impact"]["added"])
