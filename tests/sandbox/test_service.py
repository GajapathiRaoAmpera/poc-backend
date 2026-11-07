import asyncio
import os
from datetime import timedelta

import pytest

from app.core.config import get_settings
from app.core.documents import now
from app.modules.agents.code.schema import CodeFile
from app.modules.projects.models import Project
from app.modules.sandbox import docker, service
from app.modules.sandbox.spec import SandboxService, SandboxSpec
from tests.factories import login, make_user

SPEC = SandboxSpec(
    services=[
        SandboxService(
            name="backend",
            dir="backend",
            runtime="python3.12",
            install=["pip", "install", "-r", "requirements.txt"],
            start=["python", "main.py"],
            port=8000,
        )
    ]
)
FILES = [CodeFile(path="backend/main.py", content="print(1)\n")]


class FakeDocker:
    def __init__(self, inspect="true"):
        self.calls: list[list[str]] = []
        self.inspect = inspect
        self.survivors = ""

    async def __call__(self, *argv):
        argv = list(argv)
        self.calls.append(argv)
        if argv[1] == "port":
            return 0, "127.0.0.1:49999\n"
        if argv[1] == "logs":
            return 0, "Traceback: boom\n"
        if argv[1] == "ps":
            return 0, self.survivors
        if argv[1] == "inspect":
            return 0, "\n".join(self.inspect for _ in argv[4:]) + "\n"
        return 0, ""

    def index(self, pred):
        return next(i for i, c in enumerate(self.calls) if pred(c))


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeDocker()
    monkeypatch.setattr(docker, "exec", f)
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(get_settings(), "sandbox_root", tmp_path)
    monkeypatch.setattr(get_settings(), "sandbox_enabled", True)
    return f


@pytest.fixture
async def project(db):
    user = await make_user(db, email="ana@x.com")
    p = Project(owner_id=user.id, name="P")
    await db.projects.insert_one(p.to_doc())
    return p


async def test_start_removes_stale_containers(db, fake, project, monkeypatch, tmp_path):
    async def ok(_url):
        return True

    monkeypatch.setattr(service, "_probe", ok)
    state = await service.start(db, project, FILES, SPEC)
    assert state.status == "healthy"
    assert state.services[0].url == "http://127.0.0.1:49999"
    name = f"ampera-{project.id}-backend"
    rm = fake.index(lambda c: c[:3] == ["docker", "rm", "-f"] and name in c)
    run = fake.index(lambda c: c[:2] == ["docker", "run"])
    assert rm < run
    written = tmp_path / str(project.id) / "backend" / "main.py"
    assert written.read_text() == "print(1)\n"
    assert os.stat(written.parent).st_mode & 0o777 == 0o777


async def test_health_timeout_marks_failed_with_logs(db, fake, project, monkeypatch):
    clock = [0.0]

    async def never(_url):
        return False

    async def sleep(s):
        clock[0] += s

    monkeypatch.setattr(service, "_probe", never)
    monkeypatch.setattr(service, "_sleep", sleep)
    monkeypatch.setattr(service, "_clock", lambda: clock[0])
    state = await service.start(db, project, FILES, SPEC)
    assert state.status == "failed"
    assert not state.services[0].healthy
    assert "boom" in state.services[0].error
    assert 120 <= clock[0] <= 124
    assert (await service.status(project.id)).status == "failed"


async def test_start_rejects_traversal_and_missing_dir(db, fake, project):
    bad = [CodeFile(path="../evil.py", content="x")]
    state = await service.start(db, project, bad, SPEC)
    assert state.status == "failed"
    assert not any(c[1] == "run" for c in fake.calls)


async def test_disabled_or_no_docker_fails_cleanly(db, fake, project, monkeypatch):
    monkeypatch.setattr(get_settings(), "sandbox_enabled", False)
    assert (await service.start(db, project, FILES, SPEC)).status == "failed"
    monkeypatch.setattr(get_settings(), "sandbox_enabled", True)
    monkeypatch.setattr(service.shutil, "which", lambda _: None)
    state = await service.start(db, project, FILES, SPEC)
    assert state.status == "failed" and "Docker" in state.services[0].error
    assert fake.calls == []


async def test_status_reconciles_with_docker(db, fake, project, monkeypatch):
    async def ok(_url):
        return True

    monkeypatch.setattr(service, "_probe", ok)
    await service.start(db, project, FILES, SPEC)
    assert (await service.status(project.id)).status == "healthy"
    fake.inspect = "false"
    assert (await service.status(project.id)).status == "stopped"


async def test_reap_idle_stops_after_30_min(db, fake, project):
    other = Project(owner_id=project.owner_id, name="Q")
    for p, idle in ((project, 31), (other, 29)):
        await db.sandboxes.insert_one(
            {
                "_id": p.id,
                "status": "healthy",
                "services": [
                    {"name": "backend", "url": "http://127.0.0.1:1", "healthy": True, "error": None}
                ],
                "last_seen": now() - timedelta(minutes=idle),
            }
        )
    await service.reap_idle()
    assert (await db.sandboxes.find_one({"_id": project.id}))["status"] == "stopped"
    assert (await db.sandboxes.find_one({"_id": other.id}))["status"] == "healthy"
    assert any(f"ampera-{project.id}-backend" in c for c in fake.calls)
    assert not any(f"ampera-{other.id}-backend" in c for c in fake.calls)


async def test_api_owner_only(client, make_client, db, fake, project, monkeypatch):
    async def ok(_url):
        return True

    monkeypatch.setattr(service, "_probe", ok)
    await make_user(db, email="bob@x.com")
    await login(client, "ana@x.com")
    bob = make_client()
    await login(bob, "bob@x.com")
    base = f"/api/v1/projects/{project.id}/sandbox"
    r = await client.get(base)
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    assert (await bob.get(base)).status_code == 404
    assert (await bob.post(f"{base}/stop")).status_code == 404
    assert (await client.post(f"{base}/restart")).status_code == 409
    await service.start(db, project, FILES, SPEC)
    r = await client.post(f"{base}/restart")
    assert r.status_code == 200 and r.json()["status"] == "healthy"
    r = await client.get(f"{base}/logs/backend")
    assert r.status_code == 200 and "boom" in r.json()["logs"]
    assert (await bob.get(f"{base}/logs/backend")).status_code == 404
    assert (await client.get(f"{base}/logs/nope")).status_code == 404
    r = await client.post(f"{base}/stop")
    assert r.status_code == 200 and r.json()["status"] == "stopped"


async def test_fake_runner(db, project, monkeypatch):
    monkeypatch.setenv("AMPERA_FAKE_SANDBOX", "1")
    state = await service.start(db, project, FILES, SPEC)
    assert state.status == "healthy" and state.services[0].url == "http://127.0.0.1:65535"
    assert (await service.status(project.id)).status == "healthy"


async def _ok(_url):
    return True


async def test_planted_parent_dir_symlink_does_not_escape(db, fake, project, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_probe", _ok)
    outside = tmp_path / "outside"
    outside.mkdir()
    backend = tmp_path / str(project.id) / "backend"
    backend.mkdir(parents=True)
    (backend / "src").symlink_to(outside)
    files = [*FILES, CodeFile(path="backend/src/app.py", content="x\n")]
    assert (await service.start(db, project, files, SPEC)).status == "healthy"
    assert list(outside.iterdir()) == []
    assert not (backend / "src").is_symlink()
    assert (backend / "src" / "app.py").read_text() == "x\n"


async def test_planted_file_symlink_does_not_escape(db, fake, project, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_probe", _ok)
    target = tmp_path / "victim.txt"
    target.write_text("orig")
    backend = tmp_path / str(project.id) / "backend"
    backend.mkdir(parents=True)
    (backend / "main.py").symlink_to(target)
    assert (await service.start(db, project, FILES, SPEC)).status == "healthy"
    assert target.read_text() == "orig"
    assert not (backend / "main.py").is_symlink()
    assert (backend / "main.py").read_text() == "print(1)\n"


async def test_stale_symlink_from_old_manifest_is_not_followed(
    db, fake, project, monkeypatch, tmp_path
):
    monkeypatch.setattr(service, "_probe", _ok)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.py").write_text("keep")
    backend = tmp_path / str(project.id) / "backend"
    backend.mkdir(parents=True)
    (backend / "lib").symlink_to(outside)
    await db.sandboxes.insert_one(
        {"_id": project.id, "status": "stopped", "files": ["backend/lib/keep.py"]}
    )
    await service.start(db, project, FILES, SPEC)
    assert (outside / "keep.py").read_text() == "keep"


async def test_surviving_container_blocks_rewrite(db, fake, project, tmp_path):
    fake.survivors = "abc123\n"
    state = await service.start(db, project, FILES, SPEC)
    assert state.status == "failed" and "still present" in state.services[0].error
    assert not (tmp_path / str(project.id)).exists()
    assert not any(c[1] == "run" for c in fake.calls)


async def test_symlinked_mount_source_is_refused(db, fake, project, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_probe", _ok)
    await service.start(db, project, FILES, SPEC)
    # A container replaced the service folder with a link to the host root.
    backend = tmp_path / str(project.id) / "backend"
    (backend / "main.py").unlink()
    backend.rmdir()
    backend.symlink_to("/")
    fake.calls.clear()
    state = await service.restart(db, project)
    assert state.status == "failed" and "symlink" in state.services[0].error
    assert not any(c[1] == "run" for c in fake.calls)


async def test_concurrent_starts_same_project_do_not_interleave(db, fake, project, monkeypatch):
    async def slow_ok(_url):
        await asyncio.sleep(0.01)  # give the other start a chance to run mid-launch
        return True

    monkeypatch.setattr(service, "_probe", slow_ok)
    states = await asyncio.gather(*(service.start(db, project, FILES, SPEC) for _ in range(2)))
    assert [s.status for s in states] == ["healthy", "healthy"]
    rms = [i for i, c in enumerate(fake.calls) if c[:3] == ["docker", "rm", "-f"]]
    runs = [i for i, c in enumerate(fake.calls) if c[:2] == ["docker", "run"]]
    # call 1: rm ... run; call 2's removal only starts after call 1's launch.
    assert len(runs) == 2 and rms[0] < runs[0] < rms[-1] < runs[1]
    assert not any(rms[0] < i < runs[0] for i in rms[1:])


async def test_starts_for_different_projects_are_not_serialised(db, fake, project, monkeypatch):
    other = Project(owner_id=project.owner_id, name="Q")
    gate = asyncio.Event()

    real_launch = service._launch

    async def launch(db_, project_id, spec):
        if project_id == project.id:
            await gate.wait()
        return await real_launch(db_, project_id, spec)

    monkeypatch.setattr(service, "_probe", _ok)
    monkeypatch.setattr(service, "_launch", launch)
    first = asyncio.create_task(service.start(db, project, FILES, SPEC))
    await asyncio.sleep(0.05)
    # project 1 is stuck inside its lock; project 2 must still finish.
    assert (await asyncio.wait_for(service.start(db, other, FILES, SPEC), 5)).status == "healthy"
    gate.set()
    assert (await first).status == "healthy"


async def test_stop_reports_failed_when_removal_fails(db, fake, project, monkeypatch):
    monkeypatch.setattr(service, "_probe", _ok)
    await service.start(db, project, FILES, SPEC)
    fake.survivors = "abc123\n"
    state = await service.stop(project.id)
    assert state.status == "failed" and "still present" in state.services[0].error
    assert (await db.sandboxes.find_one({"_id": project.id}))["status"] == "failed"
