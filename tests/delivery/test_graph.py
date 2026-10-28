import pytest

from app.llm.base import ProviderError
from app.llm.fixtures import default_output
from app.modules.agents.requirements.service import save_answers
from app.modules.artifacts import service as artifacts
from app.modules.projects.models import Project
from tests.agents.helpers import good_architecture, good_requirements, setup_project
from tests.delivery.conftest import scripted
from tests.factories import make_user

NO_QUESTIONS = scripted(requirements_output=good_requirements(open_questions=[]))


async def test_intake_to_requirements_approval_gate(db, harness):
    _, project = await setup_project(db)
    h = harness(project, NO_QUESTIONS)
    pending = await h.start()
    assert pending == {
        "kind": "approval",
        "stage": "requirements",
        "payload": {"stage": "requirements", "revision": 1},
    }
    orch, req = await h.runs("orchestrator"), await h.runs("requirements")
    assert [r["status"] for r in orch] == ["SUCCEEDED"]
    assert [r["status"] for r in req] == ["SUCCEEDED"]
    assert req[0]["graph_key"].startswith(f"{project.id}:") and req[0]["mode"] == "generate"
    assert req[0]["active"] is False and req[0]["provider"] == "mock"


async def test_blocking_questions_pause_awaiting_answers(db, harness):
    _, project = await setup_project(db)
    h = harness(project)
    pending = await h.start()
    assert pending["kind"] == "awaiting_answers" and pending["stage"] == "requirements"


async def test_answers_resume_revises(db, harness):
    owner, project = await setup_project(db)
    h = harness(project)
    await h.start()
    await save_answers(db, project, owner, {"Q-001": "40 technicians"}, base_revision=1)
    pending = await h.resume("answers")
    req = await h.runs("requirements")
    assert [r["mode"] for r in req] == ["generate", "revise"]
    assert pending == {
        "kind": "approval",
        "stage": "requirements",
        "payload": {"stage": "requirements", "revision": 3},
    }


async def test_resume_after_approval_runs_architecture(db, harness):
    owner, project = await setup_project(db)
    h = harness(
        project,
        scripted(
            requirements_output=good_requirements(open_questions=[]),
            architecture_output=good_architecture(),
        ),
    )
    await h.start()
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    pending = await h.resume("approved:requirements")
    assert pending["kind"] == "approval" and pending["stage"] == "architecture"
    assert len(h.calls("orchestrator_output")) == 2  # re-planned with approved requirements
    arch = await h.runs("architecture")
    assert [(r["status"], r["mode"]) for r in arch] == [("SUCCEEDED", "generate")]


def _without_plan_and_budget(system, user):
    out = default_output("orchestrator_output", system, user)
    for s in out["content"]["stages"]:
        if s["stage"] in ("plan", "budget", "code"):
            s.update(include=False, inputs=[], approval_required=False)
    return out


async def test_all_approved_ends_graph(db, harness):
    owner, project = await setup_project(db)
    h = harness(
        project,
        scripted(
            requirements_output=good_requirements(open_questions=[]),
            architecture_output=good_architecture(),
            orchestrator_output=_without_plan_and_budget,
        ),
    )
    await h.start()
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    await h.resume("approved:requirements")
    await artifacts.approve(db, project, "architecture", 1, owner, None)
    assert await h.resume("approved:architecture") is None
    assert (await h.graph.aget_state(h.cfg)).next == ()


async def test_changes_requested_reruns_with_comment(db, harness):
    owner, project = await setup_project(db)
    h = harness(project, NO_QUESTIONS)
    await h.start()
    await artifacts.request_changes(db, project, "requirements", 1, owner, "Add SSO login")
    await h.resume("changes_requested:requirements")
    req = await h.runs("requirements")
    assert [r["mode"] for r in req] == ["generate", "revise"]
    assert req[1]["instruction"] == "Add SSO login"
    assert "Add SSO login" in h.calls("requirements_output")[-1]["user"]


async def test_intake_questions_gate_then_user_message_replans(db, harness):
    owner = await make_user(db)
    project = Project(owner_id=owner.id, name="x", name_source="intake")
    await db.projects.insert_one(project.to_doc())
    msg = {"project_id": project.id, "channel": "intake", "role": "user"}
    await db.messages.insert_one({**msg, "seq": 1, "text": "[ask] A field service app"})
    h = harness(project)
    pending = await h.start()
    assert pending["kind"] == "intake_questions"
    assert pending["payload"]["questions"][0]["id"] == "I-001"
    assert await h.runs("requirements") == []
    await db.messages.insert_one({**msg, "seq": 2, "text": "40 technicians, 2 dispatchers"})
    pending = await h.resume("user_message", "40 technicians, 2 dispatchers")
    assert len(h.calls("orchestrator_output")) == 2
    assert pending["kind"] == "awaiting_answers"
    spec = await db.project_specifications.find_one({"project_id": project.id})
    assert spec["origin"] == "orchestrator"


async def test_quota_gate(db, harness):
    owner, project = await setup_project(db)
    await db.users.update_one({"_id": owner.id}, {"$set": {"daily_run_limit": 1}})
    h = harness(project, NO_QUESTIONS)
    pending = await h.start()
    assert pending["kind"] == "quota" and pending["stage"] == "requirements"
    assert await h.runs("requirements") == []


async def test_failed_stage_replans_then_needs_input(db, harness):
    _, project = await setup_project(db)
    h = harness(project, scripted(requirements_output={"content": {}, "summary": "bad"}))
    pending = await h.start()
    assert pending["kind"] == "needs_input" and pending["stage"] == "requirements"
    req = await h.runs("requirements")
    assert [r["status"] for r in req] == ["FAILED"] * 3
    assert req[0]["error"]["code"] == "OUTPUT_INVALID"
    assert len(h.calls("orchestrator_output")) == 4  # initial plan + a re-plan after each failure


async def test_user_action_resets_loop_budget(db, harness):
    _, project = await setup_project(db)
    h = harness(project, scripted(requirements_output={"content": {}, "summary": "bad"}))
    await h.start()
    pending = await h.resume("retry")
    assert pending["kind"] == "needs_input"
    assert len(await h.runs("requirements")) == 6


async def test_retryable_error_reuses_run_on_reinvoke(db, harness):
    _, project = await setup_project(db)
    failing = {"n": 0}

    def flaky(system, user):
        failing["n"] += 1
        if failing["n"] == 1:
            raise ProviderError("RATE_LIMITED", "slow down", retryable=True)
        return good_requirements(open_questions=[])

    h = harness(project, scripted(requirements_output=flaky))
    with pytest.raises(ProviderError):
        await h.start()
    req = await h.runs("requirements")
    assert len(req) == 1 and req[0]["status"] == "RUNNING"
    await h.graph.ainvoke(None, h.cfg, context=h.deps())  # what a job retry does
    req = await h.runs("requirements")
    assert len(req) == 1 and req[0]["status"] == "SUCCEEDED"
    assert len(req[0]["attempts"]) == 2
    assert len(h.calls("orchestrator_output")) == 1  # completed node not re-run


async def test_node_reuses_succeeded_run_by_graph_key(db, harness):
    from app.modules.delivery.nodes import run_agent_node

    _, project = await setup_project(db)
    h = harness(project, NO_QUESTIONS)
    kw = dict(stage="requirements", agent="requirements_analyst", mode="generate", instruction=None)
    first = await run_agent_node(h.deps(), project, graph_key="k:1:requirements", **kw)
    again = await run_agent_node(h.deps(), project, graph_key="k:1:requirements", **kw)
    assert first.outcome == again.outcome == "succeeded" and first.run_id == again.run_id
    assert len(h.calls("requirements_output")) == 1
