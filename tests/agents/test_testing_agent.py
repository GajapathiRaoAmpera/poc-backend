from contextlib import asynccontextmanager

import pytest

from app.llm.mock import MockProvider
from app.modules import mcp
from app.modules.agents import design, implementation, testing
from app.modules.agents.design.mock_fixture import design_output
from app.modules.agents.testing import agent, tools
from app.modules.agents.testing.schema import Step
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import Artifact, SourceRef
from app.modules.mcp.fake import FakeMcp
from app.modules.media import service as media
from app.modules.orchestration.models import AgentRun
from app.modules.sandbox import service as sandbox
from tests.agents.helpers import approved_requirements, setup_project
from tests.delivery.conftest import scripted

ORIGIN = "http://127.0.0.1:8000"
SPEC = {
    "services": [
        {
            "name": "api",
            "dir": "api",
            "runtime": "python3.12",
            "install": ["pip", "install"],
            "start": ["python", "main.py"],
            "port": 8000,
        }  # fmt: skip
    ]
}


def step(tool, **args):
    return {"tool": tool, "args": args}


@pytest.fixture
async def ready(db, monkeypatch):
    for mod in (design, implementation, testing):
        mod.register()
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    reqs = SourceRef(kind="requirements", revision=2)
    d = design_output("", "<requirements>\nFR-001 | MUST | functional | Upload: x\n</requirements>")
    await artifacts.add_revision(
        db, project, "design", d | {"screenshots": []},
        sources=[reqs], origin="agent", summary="d", created_by=None,
    )  # fmt: skip
    impl = {
        "summary": "i", "sandbox": SPEC, "health": [], "components_done": ["api"],
        "files": [{"path": "api/main.py", "content": "print(1)\n"}],
    }  # fmt: skip
    await artifacts.add_revision(
        db, project, "implementation", impl,
        sources=[reqs, SourceRef(kind="code", revision=1), SourceRef(kind="plan", revision=1)],
        origin="agent", summary="i", created_by=None,
    )  # fmt: skip

    for kind in ("design", "implementation"):  # approved upstream, without running their gates
        await db.artifacts.update_one(
            {"project_id": project.id, "kind": kind}, {"$set": {"approved_revision": 1}}
        )
    for kind in ("code", "plan"):
        art = Artifact(project_id=project.id, owner_id=owner.id, kind=kind, approved_revision=1)
        await db.artifacts.insert_one(art.to_doc())

    async def healthy(pid):
        return sandbox.SandboxState(
            status="healthy", services=[sandbox.ServiceState(name="api", url=ORIGIN, healthy=True)]
        )

    monkeypatch.setattr(sandbox, "status", healthy)
    return project, owner


@pytest.fixture
def browser(monkeypatch):
    fake = FakeMcp({"browser_snapshot": "- button [ref=e1]"})

    @asynccontextmanager
    async def cm():
        yield fake

    monkeypatch.setattr(mcp, "get_browser", cm)
    return fake


async def go(db, project, script):
    refs = [
        SourceRef(kind="implementation", revision=1),
        SourceRef(kind="requirements", revision=2),
        SourceRef(kind="design", revision=1),
    ]
    run = AgentRun(
        project_id=project.id, owner_id=project.owner_id, stage="testing", agent=agent.NAME,
        mode="generate", inputs=refs, created_by=project.owner_id,
    )  # fmt: skip
    provider = MockProvider(script=script)

    async def emit(*a):
        pass

    ctx = agent.AgentContext(db, project, run, provider, emit, lambda: False)
    out = await agent.run(ctx)
    rev = await artifacts.get_revision(db, project, "testing", out.output["revision"])
    return rev, provider


def steps_of(provider):
    return [c for c in provider.calls if c["schema_name"] == "test_step"]


async def test_step_loop_passes_case(db, ready, browser):
    project, owner = ready
    rev, provider = await go(db, project, scripted())
    c = rev.content
    assert (c["passed"], c["failed"], c["must_failed"]) == (2, 0, [])
    assert [r["steps_used"] for r in c["results"]] == [3, 3]
    assert [t for t, _ in browser.calls[:3]] == [
        "browser_navigate", "browser_snapshot", "browser_take_screenshot",
    ]  # fmt: skip
    assert {s.kind for s in rev.sources} == {
        "implementation", "requirements", "design", "code", "plan",
    }  # fmt: skip
    media_id = c["results"][0]["screenshot_media_id"]
    assert (await media.get(db, project, media_id))[1] == "image/png"
    await artifacts.approve(db, project, "testing", rev.revision, owner, None)


async def test_navigate_outside_sandbox_refused(db, ready, browser):
    project, _ = ready
    evil = step("browser_navigate", url="https://evil.example/")
    verdict = step("report_result", passed="false", evidence="blocked")
    seq = iter([evil, verdict] * 2)
    rev, provider = await go(db, project, scripted(test_step=lambda s, u: next(seq)))
    assert not any(t == "browser_navigate" for t, _ in browser.calls)
    assert "Refused" in steps_of(provider)[1]["user"]
    assert rev.content["results"][0]["steps_used"] == 2


async def test_case_fails_after_15_steps(db, ready, browser):
    project, owner = ready
    rev, provider = await go(db, project, scripted(test_step=step("browser_snapshot")))
    r = rev.content["results"][0]
    assert r["evidence"] == "No verdict within 15 steps" and r["steps_used"] == 15
    assert len(steps_of(provider)) == 30
    assert rev.content["must_failed"] == ["FR-001", "NFR-001"]
    with pytest.raises(artifacts.NotApprovable) as exc:
        await artifacts.approve(db, project, "testing", rev.revision, owner, None)
    assert "FR-001" in str(exc.value)


async def test_tool_result_truncated_to_3000(db, ready, browser):
    project, _ = ready
    browser.script["browser_snapshot"] = "y" * 5000
    _, provider = await go(db, project, scripted())
    second = steps_of(provider)[2]["user"]
    assert "y" * 3000 in second and "y" * 3001 not in second


async def test_mcp_failure_fails_cases_with_evidence(db, ready, monkeypatch):
    project, _ = ready

    @asynccontextmanager
    async def boom():
        raise RuntimeError("npx missing")
        yield

    monkeypatch.setattr(mcp, "get_browser", boom)
    rev, provider = await go(db, project, scripted())
    assert rev.content["failed"] == 2 and "npx missing" in rev.content["results"][0]["evidence"]
    assert not steps_of(provider)


async def test_unhealthy_sandbox_that_cannot_start_fails_cases(db, ready, browser, monkeypatch):
    project, _ = ready

    async def stopped(pid):
        return sandbox.SandboxState(status="stopped")

    async def start(*a):
        raise RuntimeError("docker missing")

    monkeypatch.setattr(sandbox, "status", stopped)
    monkeypatch.setattr(sandbox, "start", start)
    rev, _ = await go(db, project, scripted())
    assert "docker missing" in rev.content["results"][0]["evidence"] and not browser.calls


def test_guard_compares_scheme_host_port():
    ok = {ORIGIN}
    g = lambda url, tool="browser_navigate": tools.guard(Step(tool=tool, args={"url": url}), ok)  # noqa: E731
    assert g(ORIGIN + "/x?y=1") is None and g(ORIGIN, "http_request") is None
    for bad in ("http://127.0.0.1:9000", "https://127.0.0.1:8000", "http://127.0.0.1:8000@evil.io",
                "file:///etc/passwd", ""):  # fmt: skip
        assert g(bad), bad
    assert tools.guard(Step(tool="browser_click", args={"target": "e1"}), ok) is None


async def test_send_failures_starts_implementation_with_direction(db, client, ready, monkeypatch):
    from app.modules.orchestration import service
    from tests.factories import login

    project, owner = ready
    content = {
        "summary": "s", "passed": 0, "failed": 1, "must_failed": ["FR-001"],
        "results": [{
            "id": "TC-001", "requirement_id": "FR-001", "title": "Upload works", "kind": "ui",
            "steps": [], "passed": False, "evidence": "Button missing", "steps_used": 3,
        }],
    }  # fmt: skip
    await artifacts.add_revision(
        db, project, "testing", content,
        sources=[SourceRef(kind="implementation", revision=1)],
        origin="agent", summary="t", created_by=None,
    )  # fmt: skip
    seen = {}

    async def fake_start(db_, project_, stage, mode, user, instruction=None, **kw):
        seen.update(stage=stage, instruction=instruction)
        return AgentRun(
            project_id=project.id, owner_id=owner.id, stage=stage, agent="implementer",
            mode=mode, inputs=[], instruction=instruction, created_by=owner.id,
        ), True  # fmt: skip

    monkeypatch.setattr(service, "start_stage", fake_start)
    await login(client, owner.email)
    r = await client.post(f"/api/v1/projects/{project.id}/stages/testing/send-failures")
    assert r.status_code == 202, r.text
    assert seen["stage"] == "implementation"
    assert "Upload works" in seen["instruction"] and "Button missing" in seen["instruction"]


async def test_provider_error_is_not_reported_as_browser_failure(db, ready, browser):
    from app.llm.base import ProviderError

    project, _ = ready
    with pytest.raises(ProviderError):
        await go(db, project, scripted(test_step=ProviderError("x", "boom", retryable=False)))


async def test_browser_close_error_keeps_results(db, ready, monkeypatch):
    project, _ = ready
    fake = FakeMcp({})

    class Closing:
        async def __aenter__(self):
            return fake

        async def __aexit__(self, *a):
            raise RuntimeError("close failed")

    monkeypatch.setattr(mcp, "get_browser", Closing)
    rev, _ = await go(db, project, scripted())
    assert rev.content["passed"] == 2


def test_long_stages_get_longer_job_timeout():
    from app.core.config import get_settings
    from app.modules.orchestration.service import job_timeout

    base = get_settings().agent_timeout_s
    assert job_timeout("testing") == job_timeout("implementation") == 5 * base
    assert job_timeout("plan") == base
