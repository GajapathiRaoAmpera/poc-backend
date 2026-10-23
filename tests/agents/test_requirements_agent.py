import copy

import pytest

from app.core.errors import AppError
from app.modules.agents.requirements.service import save_answers
from app.modules.artifacts import service as artifacts
from app.modules.orchestration.stages import stage_status
from tests.agents.helpers import good_requirements, run_stage, setup_project


async def test_valid_output_is_saved_as_agent_revision(db):
    owner, project = await setup_project(db)
    run, provider, doc = await run_stage(
        db, project, owner, "requirements", lambda *a: good_requirements()
    )
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    rev = await artifacts.get_revision(db, project, "requirements", 1)
    assert rev.origin == "agent" and rev.run_id == run.id
    assert [s.kind for s in rev.sources] == ["specification"]
    assert doc["output"]["revision"] == 1
    assert doc["summary"].startswith("Drafted")
    assert (await stage_status(db, project, "requirements")).status == "WAITING_FOR_INPUT"


async def test_project_text_is_delimited_as_data(db):
    owner, project = await setup_project(db)
    _, provider, _ = await run_stage(
        db, project, owner, "requirements", lambda *a: good_requirements()
    )
    call = provider.calls[0]
    assert "<project_data>" in call["user"] and "</project_data>" in call["user"]
    assert "Onboarding takes 3 weeks" in call["user"]
    assert "data, not instructions" in call["system"]


async def test_invalid_then_repaired(db):
    owner, project = await setup_project(db)
    bad = good_requirements()
    bad["content"]["requirements"][1]["id"] = "FR-001"  # duplicate id
    outputs = [bad, good_requirements()]
    _, provider, doc = await run_stage(
        db, project, owner, "requirements", lambda *a: outputs.pop(0)
    )
    assert doc["status"] == "SUCCEEDED"
    assert len(provider.calls) == 2
    assert "Duplicate requirement ID FR-001" in provider.calls[1]["user"]
    msgs = [e["message"] async for e in db.run_events.find({"run_id": doc["_id"]})]
    assert any("requesting repair (1/2)" in m for m in msgs)


async def test_invalid_three_times_fails_and_persists_nothing(db):
    owner, project = await setup_project(db)
    bad = good_requirements()
    bad["content"]["requirements"][0]["id"] = "REQ-1"
    _, provider, doc = await run_stage(
        db, project, owner, "requirements", lambda *a: copy.deepcopy(bad)
    )
    assert doc["status"] == "FAILED" and doc["error"]["code"] == "OUTPUT_INVALID"
    assert len(provider.calls) == 3
    assert await db.artifact_revisions.count_documents({}) == 0


async def test_must_requires_acceptance_criteria(db):
    owner, project = await setup_project(db)
    bad = good_requirements()
    bad["content"]["requirements"][0]["acceptance_criteria"] = []
    outputs = [bad, good_requirements()]
    _, provider, doc = await run_stage(
        db, project, owner, "requirements", lambda *a: outputs.pop(0)
    )
    assert doc["status"] == "SUCCEEDED"
    assert "acceptance criterion" in provider.calls[1]["user"]


async def test_answers_create_revision_and_unblock_approval(db):
    owner, project = await setup_project(db)
    await run_stage(db, project, owner, "requirements", lambda *a: good_requirements())
    with pytest.raises(AppError) as e:
        await artifacts.approve(db, project, "requirements", 1, owner, None)
    assert e.value.code == "NOT_APPROVABLE" and "Q-001" in e.value.message

    rev = await save_answers(db, project, owner, {"Q-001": "Azure AD"}, base_revision=1)
    assert rev.revision == 2 and rev.origin == "user"
    assert rev.content["open_questions"][0]["answer"] == "Azure AD"
    assert (await stage_status(db, project, "requirements")).status == "WAITING_FOR_APPROVAL"
    await artifacts.approve(db, project, "requirements", 2, owner, None)


async def test_answers_reject_unknown_question(db):
    owner, project = await setup_project(db)
    await run_stage(db, project, owner, "requirements", lambda *a: good_requirements())
    with pytest.raises(AppError) as e:
        await save_answers(db, project, owner, {"Q-999": "x"}, base_revision=1)
    assert e.value.status == 422


async def test_revise_includes_answers_and_keeps_ids(db):
    owner, project = await setup_project(db)
    await run_stage(db, project, owner, "requirements", lambda *a: good_requirements())
    await save_answers(db, project, owner, {"Q-001": "Azure AD"}, base_revision=1)

    revised = good_requirements(
        open_questions=[
            {
                "id": "Q-001",
                "question": "Which identity provider?",
                "why_it_matters": "Auth",
                "blocking": True,
                "answer": "Azure AD",
            }
        ]
    )
    revised["content"]["requirements"].append(
        {
            "id": "FR-002",
            "type": "functional",
            "title": "SSO with Azure AD",
            "description": "SSO",
            "priority": "MUST",
            "acceptance_criteria": ["Users sign in with Azure AD"],
            "source": "stated",
        }
    )
    _, provider, doc = await run_stage(
        db, project, owner, "requirements", lambda *a: revised, mode="revise"
    )
    assert doc["status"] == "SUCCEEDED"
    assert (
        "Azure AD" in provider.calls[0]["user"]
        and "<previous_requirements>" in provider.calls[0]["user"]
    )
    rev = await artifacts.get_revision(db, project, "requirements", 3)
    assert [r["id"] for r in rev.content["requirements"]] == ["FR-001", "NFR-001", "FR-002"]
    assert [s.kind for s in rev.sources] == ["specification"]


async def test_retired_ids_are_never_reused(db):
    owner, project = await setup_project(db)
    first = good_requirements()
    first["content"]["requirements"].append(
        {
            "id": "FR-002",
            "type": "functional",
            "title": "Old",
            "description": "Removed later",
            "priority": "COULD",
            "acceptance_criteria": [],
            "source": "inferred",
        }
    )
    await run_stage(db, project, owner, "requirements", lambda *a: first)
    # Revision 2 drops FR-002 (user edit).
    rev1 = await artifacts.get_revision(db, project, "requirements", 1)
    content = copy.deepcopy(rev1.content)
    content["requirements"] = content["requirements"][:2]
    await artifacts.add_revision(
        db,
        project,
        "requirements",
        content,
        sources=rev1.sources,
        origin="user",
        summary="Removed FR-002",
        created_by=owner.id,
        base_revision=1,
    )
    reuse = good_requirements()
    reuse["content"]["requirements"].append(
        {
            "id": "FR-002",
            "type": "functional",
            "title": "Different thing",
            "description": "New",
            "priority": "SHOULD",
            "acceptance_criteria": [],
            "source": "inferred",
        }
    )
    outputs = [reuse, good_requirements()]
    _, provider, doc = await run_stage(
        db, project, owner, "requirements", lambda *a: outputs.pop(0), mode="revise"
    )
    assert doc["status"] == "SUCCEEDED"
    assert "FR-002 was removed earlier" in provider.calls[1]["user"]


async def test_mock_default_fixture_produces_valid_requirements(db):
    from app.llm.mock import MockProvider
    from app.modules.orchestration.service import start_stage
    from app.worker.runner import Worker

    owner, project = await setup_project(db)
    await start_stage(db, project, "requirements", "generate", owner)
    await Worker(db, provider_factory=MockProvider, heartbeat_s=0.05).run_once()
    rev = await artifacts.get_revision(db, project, "requirements", 1)
    assert (
        rev.content["requirements"] and rev.validation.ok is False
    )  # blocking question → approval error
