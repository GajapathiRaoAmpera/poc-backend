import uuid
from datetime import timedelta

import pytest

from app.core.documents import now
from app.llm.mock import MockProvider
from app.modules.artifacts import service as artifacts
from app.modules.delivery.threads import get_thread
from app.modules.orchestration.service import start_stage
from app.modules.projects.models import Project
from app.worker.runner import Worker
from tests.agents.helpers import good_architecture, good_requirements
from tests.delivery.conftest import scripted

API = "/api/v1"
NO_Q = scripted(
    requirements_output=good_requirements(open_questions=[]),
    architecture_output=good_architecture(),
)


@pytest.fixture
def work(db):
    async def _work(script=None):
        provider = MockProvider(script=script)
        return await Worker(
            db, provider_factory=lambda: provider, heartbeat_s=0.05, poll_s=0.01
        ).run_once()

    return _work


async def _intake(client, text="A field service app for technicians"):
    r = await client.post(f"{API}/projects/intake", json={"message": text})
    return r.json()["project"]["id"]


async def _messages(client, pid, channel):
    r = await client.get(f"{API}/projects/{pid}/conversations/{channel}/messages")
    assert r.status_code == 200
    return r.json()


async def _send(client, pid, channel, text):
    return await client.post(
        f"{API}/projects/{pid}/conversations/{channel}/messages", json={"text": text}
    )


async def _project(db, pid) -> Project:
    return Project.from_doc(await db.projects.find_one({"_id": uuid.UUID(pid)}))


async def test_intake_message_resumes_paused_graph(owner_client, db, work):
    pid = await _intake(owner_client, "[ask] A field service app")
    await work()
    assert (await get_thread(db, await _project(db, pid))).pending["kind"] == "intake_questions"
    msgs = await _messages(owner_client, pid, "intake")
    assert [m["role"] for m in msgs][:2] == ["user", "agent"]
    assert "Who will use this" in msgs[-1]["text"]  # the orchestrator's question
    r = await _send(owner_client, pid, "intake", "40 technicians, 2 dispatchers")
    assert r.status_code == 202 and r.json()["message"]["seq"] == len(msgs) + 1
    job = await db.jobs.find_one({"type": "graph_step", "status": "QUEUED"})
    assert job["input"] == {
        "mode": "resume",
        "trigger": "user_message",
        "message": "40 technicians, 2 dispatchers",
    }
    await work()
    assert (await get_thread(db, await _project(db, pid))).pending["kind"] == "awaiting_answers"


async def test_requirements_chat_answers_questions(owner_client, db, work):
    pid = await _intake(owner_client)
    await work()  # requirements r1 with a blocking question
    r = await _send(owner_client, pid, "requirements", "About 40 technicians")
    assert r.status_code == 202 and r.json()["run"]["mode"] == "chat"
    await work()  # the chat run
    rev = await artifacts.latest_revision(db, await _project(db, pid), "requirements")
    assert (
        rev.revision == 2 and rev.content["open_questions"][0]["answer"] == "About 40 technicians"
    )
    msgs = await _messages(owner_client, pid, "requirements")
    agent = [m for m in msgs if m["role"] == "agent"][-1]
    assert agent["agent"] == "requirements_analyst" and agent["refs"]["artifact_revision"] == 2
    # the chat run finishing nudged the paused graph
    job = await db.jobs.find_one({"type": "graph_step", "status": "QUEUED"})
    assert job["input"]["trigger"] == "chat_finished"


async def test_requirements_chat_revise_creates_draft(owner_client, db, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    r = await _send(owner_client, pid, "requirements", "Please add offline support")
    assert r.status_code == 202
    await work()
    project = await _project(db, pid)
    rev = await artifacts.latest_revision(db, project, "requirements")
    assert rev.revision == 2 and rev.origin == "agent"
    run = await db.agent_runs.find_one({"mode": "chat"})
    assert run["status"] == "SUCCEEDED" and run["output"]["revision"] == 2


async def test_architecture_chat_proposes_change_set(owner_client, db, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    base = f"{API}/projects/{pid}"
    await owner_client.post(f"{base}/artifacts/requirements/revisions/1/approve", json={})
    await work(NO_Q)  # graph: architecture r1 → approval gate
    r = await _send(owner_client, pid, "architecture", "Add Redis between the API and the worker")
    assert r.status_code == 202
    await work(NO_Q)
    cs = await db.change_sets.find_one({})
    assert cs["status"] == "PROPOSED" and cs["base_revision"] == 1
    agent = [m for m in await _messages(owner_client, pid, "architecture") if m["role"] == "agent"]
    assert agent[-1]["refs"]["change_set_id"] == str(cs["_id"])


async def test_architecture_chat_generates_when_no_diagram(owner_client, db, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    project = await _project(db, pid)
    owner = await db.users.find_one({"email": "owner@x.com"})
    from app.modules.auth.models import User

    await artifacts.approve(db, project, "requirements", 1, User.from_doc(owner), None)
    r = await _send(owner_client, pid, "architecture", "Event-driven, on Azure")
    assert r.status_code == 202
    await work(NO_Q)
    rev = await artifacts.latest_revision(db, project, "architecture")
    assert rev is not None and rev.revision == 1
    run = await db.agent_runs.find_one({"mode": "chat", "stage": "architecture"})
    assert run["instruction"] == "Event-driven, on Azure"


async def test_architecture_chat_blocked_before_requirements_approved(owner_client, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    r = await _send(owner_client, pid, "architecture", "Use queues")
    assert r.status_code == 409 and r.json()["error"]["code"] == "STAGE_BLOCKED"


async def test_invalid_chat_output_fails_with_system_message(owner_client, db, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    await _send(owner_client, pid, "requirements", "hello")
    await work(scripted(requirements_chat_output={"reply": "x", "action": "bogus"}))
    run = await db.agent_runs.find_one({"mode": "chat"})
    assert run["status"] == "FAILED" and run["error"]["code"] == "OUTPUT_INVALID"
    system = [
        m for m in await _messages(owner_client, pid, "requirements") if m["role"] == "system"
    ]
    assert any("couldn't finish" in m["text"] for m in system)


async def test_one_active_chat_run_per_stage(owner_client, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    assert (await _send(owner_client, pid, "requirements", "first")).status_code == 202
    r = await _send(owner_client, pid, "requirements", "second")
    assert r.status_code == 409 and "busy" in r.json()["error"]["message"]


async def test_stage_busy_then_chat_finished_resumes(owner_client, db, work):
    pid = await _intake(owner_client)
    await work(NO_Q)  # requirements approval gate
    project = await _project(db, pid)
    from app.modules.auth.models import User

    owner = User.from_doc(await db.users.find_one({"email": "owner@x.com"}))
    await artifacts.approve(db, project, "requirements", 1, owner, None)
    chat, _ = await start_stage(
        db, project, "architecture", "chat", owner, instruction="Prefer Azure"
    )
    later = now() + timedelta(hours=1)
    await db.jobs.update_one({"run_id": chat.id}, {"$set": {"run_after": later}})
    from app.modules.delivery.service import notify

    await notify(db, project, "approved:requirements")
    await work(NO_Q)  # graph job: architecture has an active chat run
    t = await get_thread(db, project)
    assert t.pending["kind"] == "stage_busy" and t.pending["stage"] == "architecture"
    await db.jobs.update_one({"run_id": chat.id}, {"$set": {"run_after": now()}})
    await work(NO_Q)  # chat run → generates architecture r1, then nudges the graph
    await work(NO_Q)  # graph resumes
    t = await get_thread(db, project)
    assert t.pending["kind"] == "approval" and t.pending["stage"] == "architecture"


async def test_gate_posts_system_message_in_stage_channel(owner_client, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    msgs = await _messages(owner_client, pid, "requirements")

    assert any(
        m["role"] == "system"
        and "Waiting for your approval of requirements revision 1" in m["text"]
        for m in msgs
    )


async def test_message_seq_monotonic_and_after_filter(owner_client, db, work):
    pid = await _intake(owner_client)
    await work(NO_Q)
    for text in ("a", "b"):
        await _send(owner_client, pid, "intake", text)
    msgs = await _messages(owner_client, pid, "intake")
    seqs = [m["seq"] for m in msgs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    r = await owner_client.get(
        f"{API}/projects/{pid}/conversations/intake/messages", params={"after": seqs[-2]}
    )
    assert [m["seq"] for m in r.json()] == [seqs[-1]]


async def test_unknown_channel_422(owner_client):
    pid = await _intake(owner_client)
    r = await owner_client.get(f"{API}/projects/{pid}/conversations/budget/messages")
    assert r.status_code == 422


async def test_identical_consecutive_announcements_are_not_repeated(owner_client, db, work):
    from app.modules.delivery.threads import set_thread

    pid = await _intake(owner_client)
    await work(NO_Q)
    project = await _project(db, pid)
    for status in ("RUNNING", "DONE", "RUNNING", "DONE"):
        await set_thread(db, project.id, status=status, pending=None)
    done = [
        m
        for m in await _messages(owner_client, pid, "intake")
        if m["role"] == "system" and "every planned stage" in m["text"]
    ]
    assert len(done) == 1
