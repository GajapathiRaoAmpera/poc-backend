import copy

import pytest

from app.core.errors import AppError
from app.modules.artifacts import service as artifacts
from app.modules.orchestration.service import start_stage
from tests.agents.helpers import approved_requirements, good_architecture, run_stage, setup_project


async def test_generation_blocked_until_requirements_approved(db):
    owner, project = await setup_project(db)
    with pytest.raises(AppError) as e:
        await start_stage(db, project, "architecture", "generate", owner)
    assert e.value.code == "STAGE_BLOCKED"


async def test_generates_architecture_from_approved_requirements(db):
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    run, provider, doc = await run_stage(
        db, project, owner, "architecture", lambda *a: good_architecture()
    )
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    rev = await artifacts.get_revision(db, project, "architecture", 1)
    req = await artifacts.get_artifact(db, project, "requirements")
    assert [(s.kind, s.revision) for s in rev.sources] == [("requirements", req.approved_revision)]
    assert rev.validation.ok, rev.validation.issues
    assert (
        "<approved_requirements>" in provider.calls[0]["user"]
        and "FR-001" in provider.calls[0]["user"]
    )
    # Server lays nodes out in readable layers instead of trusting model coordinates.
    xs = {n["id"]: n["position"]["x"] for n in rev.content["nodes"]}
    assert xs["n_web"] < xs["n_api"] < xs["n_db"]


async def test_unknown_requirement_is_repaired(db):
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    bad = good_architecture()
    bad["content"]["nodes"][1]["requirement_ids"] = ["FR-404"]
    outputs = [bad, good_architecture()]
    _, provider, doc = await run_stage(
        db, project, owner, "architecture", lambda *a: outputs.pop(0)
    )
    assert doc["status"] == "SUCCEEDED"
    assert "FR-404" in provider.calls[1]["user"]


async def test_dangling_edge_three_times_fails_with_readable_error(db):
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    bad = good_architecture()
    bad["content"]["edges"].append(
        {
            "id": "e_ghost",
            "source": "n_api",
            "target": "n_cache",
            "label": "",
            "protocol": None,
            "data": None,
            "requirement_ids": [],
        }
    )
    _, _, doc = await run_stage(db, project, owner, "architecture", lambda *a: copy.deepcopy(bad))
    assert doc["status"] == "FAILED" and doc["error"]["code"] == "OUTPUT_INVALID"
    assert "n_cache" in doc["error"]["message"]
    assert await db.artifact_revisions.count_documents({"kind": "architecture"}) == 0


async def test_mock_default_fixture_is_valid(db):
    from app.llm.mock import MockProvider
    from app.worker.runner import Worker

    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    await start_stage(db, project, "architecture", "generate", owner)
    await Worker(db, provider_factory=MockProvider, heartbeat_s=0.05).run_once()
    rev = await artifacts.get_revision(db, project, "architecture", 1)
    assert rev.validation.ok, rev.validation.issues
