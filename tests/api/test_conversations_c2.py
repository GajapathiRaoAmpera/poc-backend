import pytest

from app.llm.mock import MockProvider
from app.worker.runner import Worker
from tests.agents.test_budget_agent import budget, ready
from tests.agents.test_plan_agent import plan
from tests.delivery.conftest import scripted
from tests.factories import login

RISK = {"id": "R-01", "description": "Slip", "impact": "high", "mitigation": "Buffer"}


@pytest.mark.parametrize("channel", ["plan", "budget"])
async def test_plan_and_budget_chat(client, db, channel):
    _, project = await ready(db)
    await login(client, "owner@x.com")
    base = f"/api/v1/projects/{project.id}/conversations/{channel}/messages"
    r = await client.post(base, json={"text": "Make it leaner"})
    assert r.status_code == 202 and r.json()["run"]["mode"] == "chat"
    provider = MockProvider(script=scripted(plan_output=plan(risks=[RISK]), budget_output=budget()))
    await Worker(db, provider_factory=lambda: provider, heartbeat_s=0.05, poll_s=0.01).run_once()
    msgs = (await client.get(base)).json()
    assert any(m["role"] == "agent" for m in msgs), msgs
