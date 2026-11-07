import json
import os
import subprocess
from urllib.parse import quote

import pytest
from cryptography.fernet import Fernet

from app.core import secret_box
from app.core.config import get_settings
from app.core.errors import AppError, StageBlocked
from app.llm.mock import MockProvider
from app.modules.agents import deploy, implementation, testing
from app.modules.agents.code.schema import CodeFile
from app.modules.agents.deploy import agent, git
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import Artifact, Revision, SourceRef
from app.modules.orchestration import stages
from app.modules.orchestration.models import AgentRun
from app.modules.orchestration.service import start_stage
from tests.agents.helpers import approved_requirements, setup_project
from tests.delivery.conftest import scripted

pytestmark = pytest.mark.git

TOKEN = "ghp_s3cr3t/T0ken+abc=xyz"
ENCODED = quote(TOKEN, safe="")
FILES = [
    {"path": "api/main.py", "content": "print(1)\n"},
    {"path": "web/package.json", "content": "{}\n"},
    {"path": "run.sh", "content": "#!/bin/sh\necho hi\n", "executable": True},
]
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
BRANCH = "ampera/client-portal-r1"


def sh(*args, cwd=None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout  # noqa: S603


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("SECRET_BOX_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def bare(tmp_path):
    path = tmp_path / "remote.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(path))
    return path


async def _raw(db, project, kind, content, sources=()):
    art = Artifact(
        project_id=project.id, owner_id=project.owner_id, kind=kind,
        latest_revision=1, approved_revision=1,
    )  # fmt: skip
    await db.artifacts.insert_one(art.to_doc())
    rev = Revision(
        artifact_id=art.id, project_id=project.id, owner_id=project.owner_id, kind=kind,
        revision=1, schema_version="1.0", content=content, origin="agent",
        sources=list(sources),
    )  # fmt: skip
    await db.artifact_revisions.insert_one(rev.to_doc())


@pytest.fixture
async def ready(db, bare):
    for mod in (implementation, testing, deploy):
        mod.register()
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    reqs = SourceRef(kind="requirements", revision=2)
    up = [SourceRef(kind=k, revision=1) for k in ("code", "design", "architecture", "plan")]
    await _raw(db, project, "code", {"project_slug": "client-portal"})
    for kind in ("design", "architecture", "plan"):
        await _raw(db, project, kind, {})
    impl = {"summary": "i", "sandbox": SPEC, "files": FILES}
    await artifacts.add_revision(
        db, project, "implementation", impl, sources=[reqs, *up],
        origin="agent", summary="i", created_by=None,
    )  # fmt: skip
    report = {
        "summary": "1 of 1 passed", "passed": 1, "failed": 0,
        "results": [{
            "id": "TC-001", "requirement_id": "FR-001", "title": "Upload", "kind": "ui",
            "steps": [], "passed": True,
        }],
    }  # fmt: skip
    await artifacts.add_revision(
        db, project, "testing", report,
        sources=[SourceRef(kind="implementation", revision=1), reqs, *up],
        origin="agent", summary="t", created_by=None,
    )  # fmt: skip
    for kind in ("implementation", "testing"):  # approved, without running their gates
        await db.artifacts.update_one(
            {"project_id": project.id, "kind": kind},
            {"$set": {"approved_revision": 1, "stale": False}},
        )
    project.repo_url, project.repo_token_enc = str(bare), secret_box.seal(TOKEN)
    await db.projects.update_one(
        {"_id": project.id},
        {"$set": {"repo_url": project.repo_url, "repo_token_enc": project.repo_token_enc}},
    )
    return project, owner


async def go(db, project, script=None):
    refs = [SourceRef(kind="implementation", revision=1), SourceRef(kind="testing", revision=1)]
    run = AgentRun(
        project_id=project.id, owner_id=project.owner_id, stage="deploy", agent=agent.NAME,
        mode="generate", inputs=refs, created_by=project.owner_id,
    )  # fmt: skip
    provider = MockProvider(script=script or scripted())
    events: list = []

    async def emit(*a):
        events.append(a)

    ctx = agent.AgentContext(db, project, run, provider, emit, lambda: False)
    try:
        out = await agent.run(ctx)
    except AppError as exc:
        return exc, provider, events
    rev = await artifacts.get_revision(db, project, "deploy", out.output["revision"])
    return rev, provider, events


async def test_push_to_bare_repo(db, ready, bare):
    project, _ = ready
    rev, _, _ = await go(db, project)
    c = rev.content
    assert c["branch"] == BRANCH and c["files"] == [f["path"] for f in FILES]
    assert sh("git", "rev-parse", BRANCH, cwd=bare).strip() == c["commit_sha"]
    tree = sh("git", "ls-tree", "-r", "--name-only", BRANCH, cwd=bare).split()
    assert sorted(tree) == sorted(c["files"])
    assert "100755 blob" in sh("git", "ls-tree", BRANCH, "run.sh", cwd=bare)
    author = sh("git", "log", "-1", "--format=%an <%ae>", BRANCH, cwd=bare)
    assert author.strip() == "Ampera <noreply@amperatech.ai>"
    assert {s.kind for s in rev.sources} == {
        "implementation", "testing", "code", "design", "architecture", "requirements", "plan",
    }  # fmt: skip


async def test_existing_branch_rejected_no_force(bare):
    files = [CodeFile(path="a.txt", content="a\n")]
    sha = await git.push(files, str(bare), TOKEN, BRANCH, "first")
    with pytest.raises(AppError) as exc:
        await git.push(files, str(bare), TOKEN, BRANCH, "second")
    assert exc.value.code == "BRANCH_EXISTS"
    assert sh("git", "rev-parse", BRANCH, cwd=bare).strip() == sha


async def test_push_error_is_scrubbed(db, ready, bare):
    project, _ = ready
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(
        f"#!/bin/sh\necho 'denied https://x-access-token:{TOKEN}@git.example/r {ENCODED}' >&2\n"
        "exit 1\n"
    )
    hook.chmod(0o755)
    exc, _, events = await go(db, project)
    assert isinstance(exc, AppError) and exc.code == "PUSH_FAILED"
    assert "denied" in exc.message
    seen = exc.message + json.dumps([list(map(str, e)) for e in events])
    assert TOKEN not in seen and ENCODED not in seen


async def test_token_never_in_prompt_events_artifact(db, ready):
    project, _ = ready
    rev, provider, events = await go(db, project)
    blob = json.dumps(provider.calls) + json.dumps(rev.content) + str(events)
    assert provider.calls and [c["schema_name"] for c in provider.calls] == ["deploy_message"]
    assert TOKEN not in blob and ENCODED not in blob
    assert "FR-001" in rev.content["commit_message"]


async def test_blocked_without_repo_credentials(db, ready):
    project, owner = ready
    reason = "Add the repository URL and token on the Requirements page"
    assert await stages.dependency_blockers(db, project, "deploy") == []
    await db.projects.update_one({"_id": project.id}, {"$set": {"repo_token_enc": None}})
    project.repo_token_enc = None
    assert await stages.dependency_blockers(db, project, "deploy") == [reason]
    with pytest.raises(StageBlocked):
        await start_stage(db, project, "deploy", "generate", owner)
    exc, provider, _ = await go(db, project)
    assert exc.code == "REPO_NOT_SET" and not provider.calls
    # an unreadable token (key rotated) fails clearly, without details
    project.repo_token_enc = "not-a-fernet-token"
    await db.projects.update_one({"_id": project.id}, {"$set": {"repo_token_enc": "garbage"}})
    exc, _, _ = await go(db, project)
    assert exc.code == "REPO_TOKEN_UNREADABLE" and exc.status == 409


def test_scrub():
    assert git.scrub(f"x {TOKEN} {ENCODED} https://u:p@h/r", TOKEN) == "x *** *** https://***@h/r"
    assert git.scrub("abc", "") == "abc"


def test_askpass_script(tmp_path):
    script = git.write_askpass(tmp_path)
    assert (script.stat().st_mode & 0o777) == 0o700
    env = {"AMPERA_GIT_TOKEN": TOKEN}
    ask = lambda prompt: subprocess.run(  # noqa: E731, S603
        [str(script), prompt], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert ask("Username for 'https://github.com': ") == "x-access-token"
    assert ask("Password for 'https://x-access-token@github.com': ") == TOKEN


async def test_temp_dirs_removed_on_success_and_failure(bare, monkeypatch):
    import tempfile

    made, real = [], tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: made.append(real(**kw)) or made[-1])
    files = [CodeFile(path="a.txt", content="a\n")]
    await git.push(files, str(bare), TOKEN, BRANCH, "first")
    with pytest.raises(AppError):
        await git.push(files, str(bare / "missing"), TOKEN, BRANCH, "x")
    assert len(made) == 2 and not any(map(os.path.exists, made))


async def test_unsafe_paths_refused(bare):
    for path in (".git/hooks/pre-commit", "../escape", "/etc/x", "a/.git/config"):
        with pytest.raises(AppError) as exc:
            await git.push([CodeFile(path=path, content="x")], str(bare), TOKEN, BRANCH, "m")
        assert exc.value.code == "UNSAFE_PATH"


def _hook(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _exists(path) -> bool:
    return os.path.exists(path)


async def test_cancelled_push_kills_git_before_cleanup(bare, tmp_path, monkeypatch):
    import asyncio
    import tempfile

    started = tmp_path / "started"
    _hook(bare / "hooks" / "pre-receive", f"touch {started}\nsleep 5\nexit 1\n")
    procs, real_exec = [], asyncio.create_subprocess_exec
    made, real_mkdtemp = [], tempfile.mkdtemp

    async def spy(*a, **kw):
        procs.append(await real_exec(*a, **kw))
        return procs[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda **kw: made.append(real_mkdtemp(**kw)) or made[-1]
    )
    files = [CodeFile(path="a.txt", content="a\n")]
    task = asyncio.create_task(git.push(files, str(bare), TOKEN, BRANCH, "m"))
    for _ in range(200):
        if _exists(started):
            break
        await asyncio.sleep(0.05)
    assert _exists(started)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert procs[-1].returncode is not None  # the push process is dead
    assert not _exists(made[0])
    refs = sh("git", "for-each-ref", "refs/heads/", cwd=bare)
    assert BRANCH not in refs


async def test_git_ignores_host_config_and_env(bare, tmp_path, monkeypatch):
    marker, envdump = tmp_path / "hook-ran", tmp_path / "env.txt"
    hooks = tmp_path / "host-hooks"
    for name in ("post-commit", "pre-push"):
        _hook(hooks / name, f"touch {marker}\n")
    gitconfig = tmp_path / "home" / ".gitconfig"
    gitconfig.parent.mkdir()
    gitconfig.write_text(f"[core]\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("HOME", str(gitconfig.parent))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("AMPERA_SECRET_PROBE", "leak")
    _hook(bare / "hooks" / "pre-receive", f"env > {envdump}\n")
    await git.push([CodeFile(path="a.txt", content="a\n")], str(bare), TOKEN, BRANCH, "m")
    assert not _exists(marker)
    env = envdump.read_text()
    assert "AMPERA_SECRET_PROBE" not in env and f"HOME={gitconfig.parent}" not in env


async def test_fake_git_remote_only_in_test_env(db, ready, bare, monkeypatch):
    """e2e: a stored https URL is pushed to a local bare repo only when ENVIRONMENT=test."""
    project, _ = ready
    url = "https://github.com/acme/app.git"
    project.repo_url = url
    await db.projects.update_one({"_id": project.id}, {"$set": {"repo_url": url}})
    monkeypatch.setenv("AMPERA_FAKE_GIT_REMOTE", str(bare))
    assert agent._remote("https://x.example/a.git") == str(bare)
    rev, _, _ = await go(db, project)
    assert rev.content["repo_url"] == url
    assert sh("git", "rev-parse", BRANCH, cwd=bare).strip() == rev.content["commit_sha"]
    monkeypatch.setattr(get_settings(), "environment", "production")
    assert agent._remote("https://x.example/a.git") == "https://x.example/a.git"
