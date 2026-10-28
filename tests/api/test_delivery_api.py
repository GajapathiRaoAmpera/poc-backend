import pytest

from app.llm.mock import MockProvider
from app.worker.runner import Worker
from tests.agents.helpers import good_requirements
from tests.delivery.conftest import scripted

API = "/api/v1"
IDEA = "A field service app so technicians stop using paper job sheets"


@pytest.fixture
def work_with(db):
    async def _work(script=None):
        provider = MockProvider(script=script)
        return await Worker(
            db, provider_factory=lambda: provider, heartbeat_s=0.05, poll_s=0.01
        ).run_once()

    return _work


NO_QUESTIONS = scripted(requirements_output=good_requirements(open_questions=[]))


async def _intake(client, **body):
    r = await client.post(f"{API}/projects/intake", json={"message": IDEA, **body})
    assert r.status_code == 201, r.text
    return r.json()


async def test_intake_creates_project_thread_and_message(owner_client, db):
    out = await _intake(owner_client)
    project = out["project"]
    assert project["name"] == IDEA[:60].rstrip() + "…"
    assert out["delivery"]["status"] == "RUNNING"
    msg = await db.messages.find_one({"project_id": __import__("uuid").UUID(project["id"])})
    assert msg["channel"] == "intake" and msg["role"] == "user" and msg["text"] == IDEA
    assert await db.jobs.count_documents({"type": "graph_step", "status": "QUEUED"}) == 1


async def test_intake_validates_message(owner_client):
    r = await owner_client.post(f"{API}/projects/intake", json={"message": "   "})
    assert r.status_code == 422


async def test_delivery_pauses_with_plan_after_worker(owner_client, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with(NO_QUESTIONS)
    d = (await owner_client.get(f"{API}/projects/{pid}/delivery")).json()
    assert d["status"] == "PAUSED"
    assert d["pending"]["kind"] == "approval" and d["pending"]["stage"] == "requirements"
    assert d["plan"]["version"] == 1 and d["plan"]["fallback"] is False
    assert d["message"].startswith("Waiting for your approval")
    project = (await owner_client.get(f"{API}/projects/{pid}")).json()
    assert project["name"] == "A field service app so"  # mock: first five words


async def test_approve_enqueues_graph_step(owner_client, db, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with(NO_QUESTIONS)
    r = await owner_client.post(
        f"{API}/projects/{pid}/artifacts/requirements/revisions/1/approve", json={}
    )
    assert r.status_code == 200
    job = await db.jobs.find_one({"type": "graph_step", "status": "QUEUED"})
    assert job["input"]["trigger"] == "approved:requirements"


async def test_request_changes_and_answers_notify(owner_client, db, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with()  # default mock: blocking question
    base = f"{API}/projects/{pid}"
    r = await owner_client.put(
        f"{base}/artifacts/requirements/answers",
        json={"answers": {"Q-001": "40 technicians"}, "base_revision": 1},
    )
    assert r.status_code == 201
    job = await db.jobs.find_one({"type": "graph_step", "status": "QUEUED"})
    assert job["input"]["trigger"] == "answers"


async def test_plans_listed(owner_client, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with(NO_QUESTIONS)
    plans = (await owner_client.get(f"{API}/projects/{pid}/plans")).json()
    assert [p["version"] for p in plans] == [1]
    one = (await owner_client.get(f"{API}/projects/{pid}/plans/1")).json()
    assert one["content"]["stages"][0]["stage"] == "requirements"
    assert (await owner_client.get(f"{API}/projects/{pid}/plans/9")).status_code == 404


async def test_preferences_saved_and_trigger_replan(owner_client, db, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with(NO_QUESTIONS)
    r = await owner_client.put(
        f"{API}/projects/{pid}/preferences", json={"deployment_preference": "Azure"}
    )
    assert r.status_code == 200
    assert r.json()["preferences"] == {"deployment_preference": "Azure"}
    job = await db.jobs.find_one({"type": "graph_step", "status": "QUEUED"})
    assert job["input"]["trigger"] == "user_message"
    await work_with(NO_QUESTIONS)
    spec = (await owner_client.get(f"{API}/projects/{pid}/specification")).json()
    assert spec["data"]["constraints"]["deployment_preference"] == "Azure"


async def test_preferences_reject_unknown_fields(owner_client):
    pid = (await _intake(owner_client))["project"]["id"]
    r = await owner_client.put(f"{API}/projects/{pid}/preferences", json={"evil": 1})
    assert r.status_code == 422


async def test_workflow_reports_not_planned_and_gate_reason(owner_client, work_with):
    excluded = scripted(
        requirements_output=good_requirements(open_questions=[]),
        orchestrator_output=lambda s, u: _without_architecture(s, u),
    )
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with(excluded)
    wf = {s["stage"]: s for s in (await owner_client.get(f"{API}/projects/{pid}/workflow")).json()}
    assert wf["architecture"]["status"] == "NOT_PLANNED"
    assert wf["requirements"]["status"] == "WAITING_FOR_APPROVAL"
    assert wf["requirements"]["reasons"] == ["Waiting for your approval of requirements revision 1"]


def _without_architecture(system, user):
    from app.llm.fixtures import default_output

    out = default_output("orchestrator_output", system, user)
    for s in out["content"]["stages"]:
        if s["stage"] in ("architecture", "plan", "budget", "code"):  # all need architecture
            s["include"] = False
            s["inputs"] = []
            s["approval_required"] = False
    return out


async def test_resume_and_cancel_errors(owner_client, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    base = f"{API}/projects/{pid}/delivery"
    r = await owner_client.post(f"{base}/resume")
    assert r.status_code == 409 and r.json()["error"]["code"] == "GRAPH_BUSY"
    r = await owner_client.post(f"{base}/cancel")
    assert r.status_code == 200 and r.json()["pending"]["kind"] == "cancelled"
    r = await owner_client.post(f"{base}/resume")
    assert r.status_code == 200 and r.json()["status"] == "RUNNING"


async def test_delivery_404_for_project_without_thread(owner_client, project_id):
    r = await owner_client.get(f"{API}/projects/{project_id}/delivery")
    assert r.status_code == 200 and r.json()["status"] == "IDLE"


async def test_graph_events_on_stream(owner_client, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with(NO_QUESTIONS)
    r = await owner_client.get(f"{API}/projects/{pid}/events", params={"max_wait": 0.1})
    assert "event: run_event" in r.text and '"type": "graph"' in r.text


async def test_manual_brief_edit_is_kept_and_does_not_replan(owner_client, db, work_with):
    pid = (await _intake(owner_client))["project"]["id"]
    await work_with(NO_QUESTIONS)
    edited = {"basics": {"name": "Field app"}, "problem": {"business_problem": "Paper job sheets"}}
    r = await owner_client.put(f"{API}/projects/{pid}/specification", json=edited)
    assert r.status_code == 201
    job = await db.jobs.find_one({"type": "graph_step", "status": "QUEUED"})
    assert job["input"]["trigger"] == "edited:specification"
    plans_before = await db.plans.count_documents({})
    await work_with(NO_QUESTIONS)
    assert await db.plans.count_documents({}) == plans_before  # no orchestrator call
    spec = (await owner_client.get(f"{API}/projects/{pid}/specification")).json()
    assert spec["data"] == edited  # the user's brief was not rewritten
    runs = [r async for r in db.agent_runs.find({"stage": "requirements"}).sort("created_at", 1)]
    assert [r["mode"] for r in runs] == ["generate", "revise"]  # stale requirements redone


async def test_retrying_a_failed_graph_run_resumes_the_workflow(owner_client, db, work_with):
    from app.llm.base import ProviderError

    pid = (await _intake(owner_client))["project"]["id"]
    too_large = ProviderError("TOO_LARGE", "too large", retryable=False)
    await work_with(scripted(orchestrator_output=too_large))
    failed = await db.agent_runs.find_one({"agent": "project_orchestrator", "status": "FAILED"})
    assert failed is not None

    r = await owner_client.post(f"{API}/projects/{pid}/runs/{failed['_id']}/retry")
    assert r.status_code == 202, r.text
    # No classic run for a graph-only agent: the delivery graph re-runs it instead.
    assert await db.agent_runs.count_documents({"agent": "project_orchestrator"}) == 1
    assert await db.jobs.count_documents({"type": "graph_step", "status": "QUEUED"}) == 1
    await work_with(NO_QUESTIONS)
    d = (await owner_client.get(f"{API}/projects/{pid}/delivery")).json()
    assert d["status"] == "PAUSED" and d["pending"]["kind"] == "approval"
