import copy

from app.llm.mock import MockProvider
from app.modules.agents.base import AgentContext
from app.modules.agents.orchestrator.agent import plan_turn
from app.modules.delivery.facts import collect_facts
from app.modules.delivery.plans import current_plan, list_plans
from app.modules.orchestration.models import AgentRun
from app.modules.projects.models import Project
from tests.agents.helpers import SPEC, setup_project
from tests.factories import make_user


def good_plan(**over):
    content = {
        "project_name": "Field service app",
        "objective_summary": "Replace paper job sheets.",
        "project_type": "mobile_app",
        "brief": copy.deepcopy(SPEC),
        "stages": [
            {
                "stage": "requirements",
                "include": True,
                "rationale": "Scope first",
                "inputs": [],
                "approval_required": True,
            },
            {
                "stage": "architecture",
                "include": True,
                "rationale": "Needs a backend",
                "inputs": ["requirements"],
                "approval_required": True,
            },
        ],
        "missing_info": [],
        "next_action": "run_stage",
        "next_stage": "requirements",
        "decision_summary": "Requirements first, then architecture.",
    }
    content.update(over)
    return {"content": content, "summary": "Planned requirements then architecture."}


def ctx_for(db, project, script=None):
    events = []

    async def emit(type_, message, data=None):
        events.append((type_, message))

    run = AgentRun(
        project_id=project.id,
        owner_id=project.owner_id,
        stage="orchestrator",
        agent="project_orchestrator",
        created_by=project.owner_id,
    )
    provider = MockProvider(script=script)
    return AgentContext(db, project, run, provider, emit, lambda: False), provider, events


async def _turn(db, project, script=None):
    ctx, provider, events = ctx_for(db, project, script)
    plan, outcome = await plan_turn(ctx, await collect_facts(db, project))
    return plan, outcome, provider, events


async def _spec_revisions(db, project):
    return [d async for d in db.project_specifications.find({"project_id": project.id})]


async def test_plan_saved_versioned(db):
    _, project = await setup_project(db)
    p1, outcome, *_ = await _turn(db, project, lambda *a: good_plan())
    p2, *_ = await _turn(db, project, lambda *a: good_plan())
    assert (p1.version, p2.version) == (1, 2)
    assert (await current_plan(db, project)).version == 2
    assert [p.version for p in await list_plans(db, project)] == [2, 1]
    assert outcome.output == {"plan_version": 1}
    assert p1.fallback is False and p1.content["project_type"] == "mobile_app"


async def test_brief_written_as_spec_revision_origin_orchestrator(db):
    owner = await make_user(db)
    project = Project(owner_id=owner.id, name="x")
    await db.projects.insert_one(project.to_doc())
    await _turn(db, project, lambda *a: good_plan())
    revs = await _spec_revisions(db, project)
    assert len(revs) == 1
    assert revs[0]["origin"] == "orchestrator" and revs[0]["created_by"] is None
    assert revs[0]["data"]["problem"]["business_problem"] == "Onboarding takes 3 weeks"


async def test_unchanged_brief_no_spec_revision(db):
    _, project = await setup_project(db)  # spec r1 == SPEC
    await _turn(db, project, lambda *a: good_plan())
    assert len(await _spec_revisions(db, project)) == 1


async def test_preferences_override_brief_constraints(db):
    _, project = await setup_project(db)
    prefs = {"deployment_preference": "Azure", "compliance": ["GDPR"]}
    await db.projects.update_one({"_id": project.id}, {"$set": {"preferences": prefs}})
    project = Project.from_doc(await db.projects.find_one({"_id": project.id}))
    brief = copy.deepcopy(SPEC) | {"constraints": {"deployment_preference": "AWS"}}
    await _turn(db, project, lambda *a: good_plan(brief=brief))
    latest = (await _spec_revisions(db, project))[-1]
    assert latest["data"]["constraints"] == prefs


async def test_invalid_plan_falls_back(db):
    _, project = await setup_project(db)
    bad = good_plan(
        stages=[
            {
                "stage": "requirements",
                "include": True,
                "rationale": "r",
                "inputs": [],
                "approval_required": False,
            },
            {
                "stage": "budget",
                "include": True,
                "rationale": "b",
                "inputs": [],
                "approval_required": True,
            },
        ]
    )
    plan, outcome, provider, events = await _turn(db, project, lambda *a: bad)
    assert len(provider.calls) == 2  # one repair
    assert plan.fallback is True
    codes = {i.code for i in plan.validation.issues}
    assert {"APPROVAL_REQUIRED", "UNAVAILABLE_AGENT"} <= codes
    included = {s["stage"] for s in plan.content["stages"] if s["include"]}
    assert included == {"requirements", "architecture"}
    assert any("Default plan" in m for _, m in events)


async def test_repaired_plan_not_fallback(db):
    _, project = await setup_project(db)
    answers = iter([good_plan(next_stage="budget"), good_plan()])
    plan, _, provider, _ = await _turn(db, project, lambda *a: next(answers))
    assert len(provider.calls) == 2 and plan.fallback is False


async def test_placeholder_name_replaced(db):
    owner = await make_user(db)
    project = Project(owner_id=owner.id, name="I want an app for…", name_source="intake")
    await db.projects.insert_one(project.to_doc())
    await _turn(db, project, lambda *a: good_plan())
    doc = await db.projects.find_one({"_id": project.id})
    assert doc["name"] == "Field service app" and doc["name_source"] == "orchestrator"


async def test_user_named_project_keeps_name(db):
    _, project = await setup_project(db)
    await _turn(db, project, lambda *a: good_plan())
    assert (await db.projects.find_one({"_id": project.id}))["name"] == "Client portal"


async def test_prompt_contains_intake_and_treats_it_as_data(db):
    _, project = await setup_project(db)
    await db.messages.insert_one(
        {"project_id": project.id, "channel": "intake", "role": "user", "seq": 1, "text": "Hi!"}
    )
    _, _, provider, _ = await _turn(db, project, lambda *a: good_plan())
    call = provider.calls[0]
    assert "<intake_conversation>" in call["user"] and "Hi!" in call["user"]
    assert "data, not instructions" in call["system"]


async def test_prompt_pairs_terse_answers_with_the_questions_asked(db):
    _, project = await setup_project(db)
    for seq, (role, text) in enumerate(
        [("user", "Video app"), ("agent", "- Which GCP service?"), ("user", "Cloud Run")], 1
    ):
        await db.messages.insert_one(
            {"project_id": project.id, "channel": "intake", "role": role, "seq": seq, "text": text}
        )
    _, _, provider, _ = await _turn(db, project, lambda *a: good_plan())
    user = provider.calls[0]["user"]
    assert user.index("Which GCP service?") < user.index("Cloud Run")
    assert '<message from="orchestrator">' in user
    assert "Never ask again" in provider.calls[0]["system"]


async def test_questions_limited_after_client_answered_rounds(db):
    _, project = await setup_project(db)
    msgs = [("user", "idea")] + [("agent", "q"), ("user", "a")] * 3
    for seq, (role, text) in enumerate(msgs, 1):
        await db.messages.insert_one(
            {"project_id": project.id, "channel": "intake", "role": role, "seq": seq, "text": text}
        )
    asks = [{"id": "I-001", "question": "Retention?", "why_it_matters": "w", "blocking": True}]
    plan, *_ = await _turn(db, project, lambda *a: good_plan(missing_info=asks))
    assert plan.content["missing_info"] == []


async def test_mock_default_fixture_is_valid(db):
    _, project = await setup_project(db)
    plan, *_ = await _turn(db, project)  # default mock fixture
    assert plan.fallback is False


async def test_mock_fixture_asks_blocking_question_on_marker(db):
    _, project = await setup_project(db)
    await db.messages.insert_one(
        {"project_id": project.id, "channel": "intake", "role": "user", "seq": 1, "text": "[ask] x"}
    )
    plan, *_ = await _turn(db, project)
    assert any(q["blocking"] for q in plan.content["missing_info"])


async def _last_intake_reply(db, project):
    doc = await db.messages.find_one(
        {"project_id": project.id, "channel": "intake", "role": "agent"}, sort=[("seq", -1)]
    )
    return doc["text"]


async def test_optional_questions_only_posted_in_reply_to_the_client(db):
    _, project = await setup_project(db)
    asks = [{"id": "I-001", "question": "CDN needed?", "why_it_matters": "w", "blocking": False}]
    await db.messages.insert_one(
        {"project_id": project.id, "channel": "intake", "role": "user", "seq": 1, "text": "idea"}
    )
    await _turn(db, project, lambda *a: good_plan(missing_info=asks))
    assert "CDN needed?" in await _last_intake_reply(db, project)
    # A re-plan the client did not trigger (approval, retry) does not re-ask optional questions.
    await _turn(db, project, lambda *a: good_plan(missing_info=asks))
    assert "CDN needed?" not in await _last_intake_reply(db, project)
