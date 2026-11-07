from contextlib import asynccontextmanager

import pytest

from app.llm.mock import MockProvider
from app.modules import mcp
from app.modules.agents import design
from app.modules.agents.design import agent
from app.modules.agents.design.mock_fixture import design_output
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.mcp.fake import FakeMcp
from app.modules.media import service as media
from app.modules.orchestration.models import AgentRun
from tests.agents.helpers import approved_requirements, good_architecture, run_stage, setup_project
from tests.delivery.conftest import scripted

HTML = "<html><body><h1>{}</h1></body></html>"


def output(n=3):
    out = design_output(
        "", "<requirements>\nFR-001 | MUST | functional | Upload: x\n</requirements>"
    )
    base = out["screens"][0]
    out["screens"] = [
        {**base, "id": f"S-{i:03}", "name": f"Screen {i}", "html": HTML.format(i)}
        for i in range(1, n + 1)
    ]
    out["flows"] = [{"from_screen": "S-001", "action": "Next", "to_screen": "S-002"}]
    return out


@pytest.fixture
async def ready(db, monkeypatch):
    design.register()
    fake = FakeMcp({})

    @asynccontextmanager
    async def browser():
        yield fake

    monkeypatch.setattr(mcp, "get_browser", browser)
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    await run_stage(db, project, owner, "architecture", lambda *a: good_architecture())
    await artifacts.approve(db, project, "architecture", 1, owner, None)
    return project, fake


async def go(db, project, script, mode="generate", instruction=None, with_design=False):
    refs = [SourceRef(kind="requirements", revision=2), SourceRef(kind="architecture", revision=1)]
    if with_design:
        refs.append(SourceRef(kind="design", revision=1))
    run = AgentRun(
        project_id=project.id, owner_id=project.owner_id, stage="design", agent=agent.NAME,
        mode=mode, inputs=refs, instruction=instruction, created_by=project.owner_id,
    )  # fmt: skip
    provider = MockProvider(script=script)

    async def emit(*a):
        pass

    from app.modules.agents.base import AgentContext

    ctx = AgentContext(db, project, run, provider, emit, lambda: False)
    return await agent.run(ctx), provider


async def test_generate_renders_one_shot_per_screen(db, ready):
    project, fake = ready
    outcome, provider = await go(db, project, scripted(design_output=output(3)))
    assert [c["schema_name"] for c in provider.calls] == ["design_output"]
    assert [t for t, _ in fake.calls].count("browser_navigate") == 3
    assert fake.calls[0][1]["url"].startswith("data:text/html;base64,")
    rev = await artifacts.get_revision(db, project, "design", outcome.output["revision"])
    shots = rev.content["screenshots"]
    assert [s["screen_id"] for s in shots] == ["S-001", "S-002", "S-003"]
    assert await db.media.count_documents({"project_id": project.id}) == 3
    data, ctype = await media.get(db, project, shots[0]["media_id"])
    assert ctype == "image/png" and data.startswith(b"\x89PNG")
    assert rev.validation.ok


async def test_chat_rewrites_one_screen_and_rerenders_only_it(db, ready):
    project, fake = ready

    await go(db, project, scripted(design_output=output(3)))
    fake.calls.clear()
    changes = {
        "summary": "Retitled.",
        "changes": [
            {"op": "write", "screen": {**output(3)["screens"][1], "html": HTML.format("New")}}
        ],
    }
    outcome, provider = await go(
        db, project, scripted(design_changes=changes), mode="chat",
        instruction="Rework S-002", with_design=True,
    )  # fmt: skip
    assert [c["schema_name"] for c in provider.calls] == ["design_chat_output", "design_changes"]
    assert [t for t, _ in fake.calls].count("browser_navigate") == 1
    rev = await artifacts.get_revision(db, project, "design", 2)
    assert "New" in rev.content["screens"][1]["html"]
    old = (await artifacts.get_revision(db, project, "design", 1)).content["screenshots"]
    new = rev.content["screenshots"]
    assert [s["screen_id"] for s in new] == ["S-001", "S-002", "S-003"]
    assert new[0] == old[0] and new[2] == old[2] and new[1] != old[1]


async def test_screenshot_failure_saves_without_shots(db, ready, monkeypatch):
    project, _ = ready

    @asynccontextmanager
    async def broken():
        raise RuntimeError("no browser")
        yield

    monkeypatch.setattr(mcp, "get_browser", broken)
    outcome, _ = await go(db, project, scripted(design_output=output(2)))
    rev = await artifacts.get_revision(db, project, "design", outcome.output["revision"])
    assert rev.content["screenshots"] == []
