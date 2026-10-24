from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.delivery.facts import collect_facts, digest
from tests.agents.helpers import good_requirements, setup_project


async def _req_revision(db, project, owner, **over):
    return await artifacts.add_revision(
        db,
        project,
        "requirements",
        good_requirements(**over)["content"],
        sources=[SourceRef(kind="specification", revision=1)],
        origin="agent",
        summary="draft",
        created_by=None,
    )


async def test_no_artifacts(db):
    _, project = await setup_project(db)
    f = await collect_facts(db, project)
    assert f.spec_revision == 1
    assert f.stages["requirements"].has_draft is False
    assert f.stages["architecture"].has_draft is False


async def test_blocking_questions_counted(db):
    owner, project = await setup_project(db)
    await _req_revision(db, project, owner)
    r = (await collect_facts(db, project)).stages["requirements"]
    assert r.has_draft and r.latest_revision == 1
    assert r.blocking_unanswered == 1
    assert r.approved_is_latest is False


async def test_changes_requested_only_for_latest(db):
    owner, project = await setup_project(db)
    await _req_revision(db, project, owner, open_questions=[])
    await artifacts.request_changes(db, project, "requirements", 1, owner, "Add SSO")
    assert (await collect_facts(db, project)).stages["requirements"].changes_requested == "Add SSO"
    await _req_revision(db, project, owner, open_questions=[])
    assert (await collect_facts(db, project)).stages["requirements"].changes_requested is None


async def test_approved_and_stale(db):
    owner, project = await setup_project(db)
    await _req_revision(db, project, owner, open_questions=[])
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    r = (await collect_facts(db, project)).stages["requirements"]
    assert r.approved_is_latest and r.approved_revision == 1 and not r.stale
    await artifacts.mark_stale(db, project, "requirements", "Spec changed")
    r = (await collect_facts(db, project)).stages["requirements"]
    assert r.stale and r.stale_reason == "Spec changed"


async def test_active_run_detected(db):
    owner, project = await setup_project(db)
    await db.agent_runs.insert_one(
        {"_id": "x", "project_id": project.id, "stage": "architecture", "active": True}
    )
    assert (await collect_facts(db, project)).stages["architecture"].active_run is True


async def test_intake_messages_and_preferences(db):
    _, project = await setup_project(db)
    await db.messages.insert_one(
        {"project_id": project.id, "channel": "intake", "role": "user", "seq": 1, "text": "Hi"}
    )
    await db.projects.update_one({"_id": project.id}, {"$set": {"preferences": {"x": 1}}})
    f = await collect_facts(db, project)
    assert f.intake_messages == ["Hi"]
    assert f.preferences == {"x": 1}


async def test_digest_excludes_content(db):
    owner, project = await setup_project(db)
    await _req_revision(db, project, owner)
    text = digest(await collect_facts(db, project))
    assert "Clients upload onboarding documents." not in text
    assert "requirements" in text and "1 blocking" in text
    assert len(text) <= 4000


async def test_unanswered_question_not_counted_as_error(db):
    owner, project = await setup_project(db)
    await _req_revision(db, project, owner)
    r = (await collect_facts(db, project)).stages["requirements"]
    assert r.blocking_unanswered == 1 and r.save_errors == 0


async def test_answered_since_agent(db):
    from app.modules.agents.requirements.service import save_answers

    owner, project = await setup_project(db)
    await _req_revision(db, project, owner)
    assert (await collect_facts(db, project)).stages["requirements"].answered_since_agent is False
    await save_answers(db, project, owner, {"Q-001": "Azure AD"}, base_revision=1)
    r = (await collect_facts(db, project)).stages["requirements"]
    assert r.answered_since_agent is True and r.blocking_unanswered == 0
    # A newer agent revision that carries the answer clears the flag.
    answered = good_requirements()["content"]["open_questions"][0] | {"answer": "Azure AD"}
    await _req_revision(db, project, owner, open_questions=[answered])
    assert (await collect_facts(db, project)).stages["requirements"].answered_since_agent is False


async def _intake(db, project, *msgs):
    for seq, (role, text) in enumerate(msgs, 1):
        await db.messages.insert_one(
            {"project_id": project.id, "channel": "intake", "role": role, "seq": seq, "text": text}
        )


async def test_intake_transcript_keeps_orchestrator_questions_in_order(db):
    _, project = await setup_project(db)
    await _intake(
        db,
        project,
        ("user", "Build a video app"),
        ("agent", "Before I continue, please answer:\n- Which hosting?"),
        ("system", "Delivery workflow failed: HTTP 429"),
        ("user", "Cloud Run"),
    )
    f = await collect_facts(db, project)
    assert f.intake_transcript == [
        ("client", "Build a video app"),
        ("orchestrator", "Before I continue, please answer:\n- Which hosting?"),
        ("client", "Cloud Run"),
    ]
    assert f.intake_messages == ["Build a video app", "Cloud Run"]


async def test_answered_rounds_counts_replies_to_orchestrator_turns(db):
    _, project = await setup_project(db)
    await _intake(
        db,
        project,
        ("user", "idea"),
        ("agent", "q1"),
        ("user", "a1"),
        ("user", "a1 more"),
        ("agent", "q2"),
        ("user", "a2"),
        ("agent", "q3"),
    )
    assert (await collect_facts(db, project)).answered_rounds == 2
