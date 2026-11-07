"""Every new agent reports its units of work as `subtask` step data (T14)."""

import pytest

from app.llm.mock import MockProvider
from app.modules.agents.base import AgentContext
from app.modules.artifacts.models import SourceRef
from app.modules.orchestration.models import AgentRun
from tests.agents import test_deploy_agent as dep
from tests.agents import test_design_agent as des
from tests.agents import test_implementation_agent as imp
from tests.agents import test_testing_agent as tst
from tests.delivery.conftest import scripted


def refs(*pairs):
    return [SourceRef(kind=k, revision=r) for k, r in pairs]


async def run(db, project, mod, stage, inputs, script):
    events: list = []

    async def emit(*a):
        events.append(a)

    run_ = AgentRun(
        project_id=project.id, owner_id=project.owner_id, stage=stage, agent=mod.NAME,
        mode="generate", inputs=inputs, created_by=project.owner_id,
    )  # fmt: skip
    ctx = AgentContext(db, project, run_, MockProvider(script=script), emit, lambda: False)
    await mod.run(ctx)
    subs = [e[2]["subtask"] for e in events if e[0] == "step" and e[2] and "subtask" in e[2]]
    final = {}
    for s in subs:
        assert s["status"] in {"running", "passed", "failed"} and s["label"]
        final[s["id"]] = s["status"]
    return subs, final


async def test_each_agent_emits_subtasks_design(db, monkeypatch):
    project, _ = await des.ready.__wrapped__(db, monkeypatch)
    subs, final = await run(
        db, project, des.agent, "design", refs(("requirements", 2), ("architecture", 1)),
        scripted(design_output=des.output(3)),
    )  # fmt: skip
    assert final == {"S-001": "passed", "S-002": "passed", "S-003": "passed"}
    assert subs[0] == {"id": "S-001", "label": "Screen 1", "status": "running"}


async def test_each_agent_emits_subtasks_implementation(db, monkeypatch):
    project, _ = await imp.ready.__wrapped__(db, monkeypatch)
    imp.FakeSandbox(monkeypatch, [["api"], []])  # unhealthy once, repaired
    inputs = refs(("code", 1), ("design", 1), ("architecture", 1), ("requirements", 2), ("plan", 1))
    subs, final = await run(db, project, imp.agent, "implementation", inputs, scripted())
    assert final == {
        "component:api": "passed", "component:web": "passed",
        "service:api": "passed", "service:web": "passed",
    }  # fmt: skip
    assert {"id": "service:api", "label": "api health", "status": "failed"} in subs


async def test_each_agent_emits_subtasks_testing(db, monkeypatch):
    project, _ = await tst.ready.__wrapped__(db, monkeypatch)
    tst.browser.__wrapped__(monkeypatch)
    verdicts = iter([
        tst.step("report_result", passed="true", evidence="ok"),
        tst.step("report_result", passed="false", evidence="no"),
    ])  # fmt: skip
    inputs = refs(("implementation", 1), ("requirements", 2), ("design", 1))
    subs, final = await run(
        db, project, tst.agent, "testing", inputs,
        scripted(test_step=lambda s, u: next(verdicts)),
    )  # fmt: skip
    assert list(final.values()) == ["passed", "failed"] and len(final) == 2
    assert all(s["status"] == "running" for s in subs[::2])


@pytest.mark.git
async def test_each_agent_emits_subtasks_deploy(db, monkeypatch, tmp_path):
    key = dep._key.__wrapped__(monkeypatch)  # reuse the sibling fixtures (called directly)
    next(key)
    try:
        project, _ = await dep.ready.__wrapped__(db, dep.bare.__wrapped__(tmp_path))
        inputs = refs(("implementation", 1), ("testing", 1))
        subs, final = await run(db, project, dep.agent, "deploy", inputs, scripted())
    finally:
        next(key, None)
    assert final == {"commit": "passed", "push": "passed"}
    assert [(s["id"], s["status"]) for s in subs] == [
        ("commit", "running"), ("commit", "passed"), ("push", "running"), ("push", "passed"),
    ]  # fmt: skip
