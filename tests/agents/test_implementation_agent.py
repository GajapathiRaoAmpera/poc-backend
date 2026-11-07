from contextlib import asynccontextmanager

import pytest

from app.llm.mock import MockProvider
from app.modules import mcp
from app.modules.agents import design, implementation
from app.modules.agents.base import AgentContext, OutputInvalid
from app.modules.agents.implementation import agent
from app.modules.agents.implementation.schema import ImplementationContent
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.mcp.fake import FakeMcp
from app.modules.orchestration.models import AgentRun
from app.modules.sandbox import service as sandbox
from tests.agents.helpers import run_stage
from tests.agents.test_code_agent import code
from tests.agents.test_code_agent import ready as scaffold_ready
from tests.delivery.conftest import scripted

FILES = [
    {"path": "api/pyproject.toml", "content": "[project]\nname='api'\n"},
    {"path": "web/package.json", "content": '{"name": "web"}\n'},
    {"path": "web/index.js", "content": "// TODO web stub\n"},
]

ALL_SOURCES = {"code", "design", "architecture", "requirements", "plan"}


def change(path, content="x\n"):
    return {"summary": "Done.", "changes": [{"op": "write", "path": path, "content": content}]}


@pytest.fixture
async def ready(db, monkeypatch):
    design.register()
    implementation.register()

    @asynccontextmanager
    async def browser():
        yield FakeMcp({})

    monkeypatch.setattr(mcp, "get_browser", browser)
    owner, project = await scaffold_ready(db)
    base = code()
    _, _, doc = await run_stage(db, project, owner, "code", scripted(code_output=base | {
        "files": base["files"] + FILES,
    }))  # fmt: skip
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    _, _, doc = await run_stage(db, project, owner, "design", scripted())
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    for kind in ("code", "design"):
        await artifacts.approve(db, project, kind, 1, owner, None)
    return project, owner


class FakeSandbox:
    """Stands in for sandbox.service: `rounds` lists the unhealthy service names per start."""

    def __init__(self, monkeypatch, rounds):
        self.rounds, self.starts = list(rounds), []
        monkeypatch.setattr(sandbox, "start", self.start)
        monkeypatch.setattr(sandbox, "logs", self.logs)

    async def start(self, db, project, files, spec):
        self.starts.append([f.path for f in files])
        bad = self.rounds.pop(0) if self.rounds else []
        services = [
            sandbox.ServiceState(name=s.name, healthy=s.name not in bad) for s in spec.services
        ]
        return sandbox.SandboxState(status="failed" if bad else "healthy", services=services)

    async def logs(self, project_id, svc, tail=200):
        return f"LOGS-OF-{svc}"


async def go(db, project, script, direction=None):
    refs = [
        SourceRef(kind="code", revision=1),
        SourceRef(kind="design", revision=1),
        SourceRef(kind="architecture", revision=1),
        SourceRef(kind="requirements", revision=2),
        SourceRef(kind="plan", revision=1),
    ]
    run = AgentRun(
        project_id=project.id, owner_id=project.owner_id, stage="implementation",
        agent=agent.NAME, mode="generate", inputs=refs, instruction=direction,
        created_by=project.owner_id,
    )  # fmt: skip
    provider = MockProvider(script=script)

    async def emit(*a):
        pass

    ctx = AgentContext(db, project, run, provider, emit, lambda: False)
    return await agent.run(ctx), provider


def names(provider):
    return [c["schema_name"] for c in provider.calls]


def test_components_are_folders_with_a_manifest():
    from app.modules.agents.code.schema import CodeFile

    paths = ["api/pyproject.toml", "web/package.json", "docs/a.md", "x/y/package.json", "R.md"]
    files = [CodeFile(path=p, content="") for p in paths]
    assert agent.components(files) == ["api", "web"]


async def test_one_call_per_component_scope_enforced(db, ready, monkeypatch):
    ready, owner = ready
    FakeSandbox(monkeypatch, [[]])
    outcome, provider = await go(db, ready, scripted())
    assert names(provider) == ["code_changes", "code_changes", "sandbox_spec"]
    rev = await artifacts.get_revision(db, ready, "implementation", outcome.output["revision"])
    assert rev.content["components_done"] == ["api", "web"]
    assert {s.kind for s in rev.sources} == ALL_SOURCES
    assert any(f["path"] == "sandbox.json" for f in rev.content["files"])
    assert rev.validation.ok

    with pytest.raises(OutputInvalid) as exc:
        await go(db, ready, scripted(code_changes=change("web/index.js")))
    assert any(i["code"] == "SCOPE" for i in exc.value.issues)


async def test_repair_sends_only_failing_service_logs_and_files(db, ready, monkeypatch):
    ready, owner = ready
    fake = FakeSandbox(monkeypatch, [["api"], []])
    _, provider = await go(db, ready, scripted())
    repair = provider.calls[-1]["user"]
    assert names(provider)[-1] == "code_changes" and "Service api" in repair
    assert "LOGS-OF-api" in repair and "LOGS-OF-web" not in repair
    assert "api/main.py" in repair and "web/index.js" not in repair
    assert len(fake.starts) == 2
    rev = await artifacts.get_revision(db, ready, "implementation", 1)
    assert all(h["healthy"] for h in rev.content["health"])


async def test_unhealthy_after_two_rounds_blocks_approval(db, ready, monkeypatch):
    ready, owner = ready
    fake = FakeSandbox(monkeypatch, [["api"]] * 3)
    _, provider = await go(db, ready, scripted())
    assert len(fake.starts) == 3 and names(provider).count("code_changes") == 2 + 2
    rev = await artifacts.get_revision(db, ready, "implementation", 1)
    assert [h["healthy"] for h in rev.content["health"]] == [False, True]
    with pytest.raises(artifacts.NotApprovable) as exc:
        await artifacts.approve(db, ready, "implementation", 1, owner.id, None)
    assert "Service api is not healthy" in str(exc.value)


async def test_sandbox_start_crash_is_unhealthy_not_a_crash(db, ready, monkeypatch):
    ready, owner = ready

    async def boom(*a, **k):
        raise RuntimeError("docker missing")

    monkeypatch.setattr(sandbox, "start", boom)
    monkeypatch.setattr(sandbox, "logs", boom)
    await go(db, ready, scripted())
    rev = await artifacts.get_revision(db, ready, "implementation", 1)
    assert not any(h["healthy"] for h in rev.content["health"])
    assert "docker missing" in rev.content["health"][0]["log_excerpt"]


async def test_digest_contains_data_model_entities(db, ready, monkeypatch):
    ready, owner = ready
    FakeSandbox(monkeypatch, [[]])
    _, provider = await go(db, ready, scripted())
    first = provider.calls[0]["user"]
    assert "users: id uuid" in first and "FR-001" in first and "Home" in first


async def test_direction_from_testing_failures_reaches_prompt(db, ready, monkeypatch):
    ready, owner = ready
    FakeSandbox(monkeypatch, [[]])
    _, provider = await go(db, ready, scripted(), direction="Failing: test_upload_pdf, test_login")
    assert all("test_upload_pdf" in c["user"] for c in provider.calls[:2])


def test_scaffold_over_the_file_limit_is_an_issue_not_an_exception():
    from app.modules.agents.code.agent import _changes_issues
    from app.modules.agents.code.schema import CodeChanges, CodeFile, FileChange

    files = [CodeFile(path=f"api/f{i}.py", content="") for i in range(60)]
    changes = CodeChanges(
        summary="x", changes=[FileChange(op="write", path="api/new.py", content="x\n")]
    )
    issues = _changes_issues(files, changes, {"api/"})  # 61 agent files
    assert [i.code for i in issues] == ["TOO_LARGE"]
    spec = {"services": [{"name": "api", "dir": "api", "runtime": "python3.12",
            "install": ["pip", "install"], "start": ["python", "m.py"], "port": 8000}]}  # fmt: skip
    content = ImplementationContent(
        summary="s", sandbox=spec, files=files + [CodeFile(path="sandbox.json", content="{}")]
    )
    assert [i.code for i in agent.save_issues(content)] == ["TOO_LARGE"]


def test_prefix_scope_rejects_siblings_absolute_and_traversal():
    from app.modules.agents.code.agent import _changes_issues
    from app.modules.agents.code.schema import CodeChanges, CodeFile, FileChange

    files = [CodeFile(path="api/a.py", content="x\n"), CodeFile(path="web/b.js", content="x\n")]
    for path, want in [("api2/x", "SCOPE"), ("/abs/x", "SCOPE"), ("api/../web/x", "PATH")]:
        ch = CodeChanges(summary="x", changes=[FileChange(op="write", path=path, content="x\n")])
        assert [i.code for i in _changes_issues(files, ch, {"api/"})] == [want], path
