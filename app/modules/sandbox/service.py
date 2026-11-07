"""Runs a generated app in locked-down Docker containers (spec §5.2) for preview and testing.

State lives in the ``sandboxes`` collection (``_id`` = project id); Docker is the source of truth
for liveness, so ``status`` reconciles with ``docker inspect``.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import stat
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.db import Db, database
from app.core.documents import now
from app.core.errors import Conflict, NotFound
from app.modules.agents.code.schema import CodeFile
from app.modules.agents.code.validate import path_issue
from app.modules.projects.models import Project
from app.modules.sandbox import docker
from app.modules.sandbox.spec import SandboxSpec, missing_dirs

HEALTH_TIMEOUT_S = 120
HEALTH_POLL_S = 2
IDLE_STOP = timedelta(minutes=30)
MAX_LOG_CHARS = 4000
FAKE_URL = "http://127.0.0.1:65535"

logger = logging.getLogger(__name__)

# Replaced by tests (fake clock / fake health check).
_clock = time.monotonic
_sleep = asyncio.sleep

# shortcut: in-process locks, single-server POC; a multi-worker deploy needs a DB/Redis lock.
_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _lock(project_id: uuid.UUID) -> asyncio.Lock:
    """Serialises start/restart/stop per project: removal, file writes and launch must not mix."""
    return _locks.setdefault(project_id, asyncio.Lock())


class ServiceState(BaseModel):
    name: str
    url: str | None = None
    healthy: bool = False
    error: str | None = None


class SandboxState(BaseModel):
    status: Literal["stopped", "starting", "healthy", "failed"]
    services: list[ServiceState] = []


def _fake() -> bool:
    return get_settings().environment == "test" and os.environ.get("AMPERA_FAKE_SANDBOX") == "1"


def _unavailable() -> str | None:
    if not get_settings().sandbox_enabled:
        return "The sandbox is disabled on this server (SANDBOX_ENABLED=false)"
    if shutil.which("docker") is None:
        return "Docker is not installed on this server"
    return None


def _workdir(pid: str) -> Path:
    # Real path, so a mount source check can compare it with realpath().
    return Path(os.path.realpath(get_settings().sandbox_root)) / pid


async def _save(db: Db, project_id: uuid.UUID, state: SandboxState, **extra) -> SandboxState:
    await db.sandboxes.update_one(
        {"_id": project_id},
        {"$set": {**state.model_dump(), "last_seen": now(), **extra}},
        upsert=True,
    )
    return state


def _failed(spec: SandboxSpec, error: str) -> SandboxState:
    return SandboxState(
        status="failed", services=[ServiceState(name=s.name, error=error) for s in spec.services]
    )


async def _probe(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=HEALTH_POLL_S) as client:
            return (await client.get(url)).status_code < 400
    except httpx.HTTPError:
        return False


_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_dir(parent: int, name: str, create: bool) -> int | None:
    """Open ``name`` under the dir fd ``parent`` without following a symlink.

    Containers can write into the work dir, so a component may be a planted symlink: with
    ``create`` it is unlinked and replaced by a real dir; without, the walk stops.
    """
    try:
        return os.open(name, _DIR, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            return None
    except OSError as exc:
        if not stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
            raise ValueError(f"{name} is not a directory") from exc
        if not create:
            return None
        os.unlink(name, dir_fd=parent)
    os.mkdir(name, 0o777, dir_fd=parent)
    fd = os.open(name, _DIR, dir_fd=parent)
    os.fchmod(fd, 0o777)  # noqa: S103 - the container user (uid 1000) installs here
    return fd


def _walk(root: int, parts: list[str], create: bool) -> int | None:
    """A dir fd for ``root/parts``; no path string is ever re-resolved."""
    fd = os.dup(root)
    for part in parts:
        nxt = _open_dir(fd, part, create)
        os.close(fd)
        if nxt is None:
            return None
        fd = nxt
    return fd


def _write_files(root: Path, files: list[CodeFile], old: list[str]) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    base = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        top = _walk(base, [root.name], create=True)
    finally:
        os.close(base)
    if top is None:
        raise ValueError(f"Cannot open the work dir {root}")
    try:
        new = {f.path for f in files}
        for path in old:  # files the previous run wrote and this manifest dropped
            if path in new:
                continue
            *parts, name = path.split("/")
            if (fd := _walk(top, parts, create=False)) is not None:
                with contextlib.suppress(OSError):
                    os.unlink(name, dir_fd=fd)
                os.close(fd)
        for f in files:
            if (issue := path_issue(f.path)) is not None:
                raise ValueError(issue.message)
            *parts, name = f.path.split("/")
            fd = _walk(top, parts, create=True)
            if fd is None:
                raise ValueError(f"Cannot open the folder for {f.path}")
            try:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=fd)  # also drops a planted symlink or hardlink
                mode = 0o755 if f.executable else 0o644
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                out_fd = os.open(name, flags, mode, dir_fd=fd)
                with os.fdopen(out_fd, "w", encoding="utf-8") as out:
                    os.fchmod(out_fd, mode)
                    out.write(f.content)
            finally:
                os.close(fd)
    finally:
        os.close(top)


def _mount_source(pid: str, rel: str) -> Path:
    """Defence in depth: the -v source must be a real dir with no symlink anywhere below the root.

    Docker follows symlinks in a bind source, which would mount an arbitrary host path.
    """
    current = _workdir(pid).parent
    for part in [pid, *rel.split("/")]:
        current = current / part
        mode = os.lstat(current).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError(f"{rel}: the service folder is a symlink or not a folder")
    if os.path.realpath(current) != str(current):
        raise ValueError(f"{rel}: the service folder does not resolve to itself")
    return current


async def _remove(pid: str, names: set[str]) -> str | None:
    """Remove this project's containers (by name and by label) and its network.

    Returns an error if any container survived: nothing may touch the work dir while one runs.
    """
    ps = ("docker", "ps", "-aq", "--filter", f"label={docker.label(pid)}")
    rc, out = await docker.exec(*ps)
    if rc == 0 and out.split():
        await docker.exec("docker", "rm", "-f", *out.split())
    error = None
    if names:  # by name too, after the label pass (one container twice in one rm fails)
        _, out = await docker.exec("docker", "rm", "-f", *sorted(names))
        lines = out.splitlines()
        errors = [x for x in lines if x.startswith("Error") and "No such container" not in x]
        if errors:
            error = f"Cannot remove the old sandbox containers: {errors[0][:500]}"
    await docker.exec("docker", "network", "rm", docker.network(pid))
    rc, out = await docker.exec(*ps)
    if rc != 0 or out.strip():
        return f"Old sandbox containers are still present: {out[-500:]}"
    return error


def _names(pid: str, doc: dict | None) -> set[str]:
    if not doc:
        return set()
    services = [*doc.get("services", []), *(doc.get("spec") or {}).get("services", [])]
    return {docker.container(pid, s["name"]) for s in services}


async def _launch(db: Db, project_id: uuid.UUID, spec: SandboxSpec) -> SandboxState:
    pid = str(project_id)
    await _save(db, project_id, SandboxState(status="starting"))
    await docker.exec(
        "docker", "network", "create", "--label", docker.label(pid), docker.network(pid)
    )
    states: list[ServiceState] = []
    for svc in spec.services:
        name = docker.container(pid, svc.name)
        try:
            _mount_source(pid, svc.dir)
        except (ValueError, OSError) as exc:
            states.append(ServiceState(name=svc.name, error=str(exc)))
            continue
        rc, out = await docker.exec(*docker.run_args(pid, svc, _workdir(pid)))
        if rc != 0:
            states.append(ServiceState(name=svc.name, error=out[-MAX_LOG_CHARS:]))
            continue
        rc, out = await docker.exec("docker", "port", name, str(svc.port))
        if rc != 0 or not out.strip():
            states.append(ServiceState(name=svc.name, error=f"No host port: {out}"[:MAX_LOG_CHARS]))
            continue
        host_port = out.split()[0].rsplit(":", 1)[1]
        states.append(ServiceState(name=svc.name, url=f"http://127.0.0.1:{host_port}"))

    health = {s.name: s.health for s in spec.services}
    deadline = _clock() + HEALTH_TIMEOUT_S
    while True:
        for st in states:
            if st.url and not st.healthy:
                st.healthy = await _probe(st.url + health[st.name])
        if all(st.healthy or not st.url for st in states) or _clock() >= deadline:
            break
        await _sleep(HEALTH_POLL_S)

    for st in states:
        if st.url and not st.healthy:
            _, out = await docker.exec(
                "docker", "logs", "--tail", "200", docker.container(pid, st.name)
            )
            msg = f"Not healthy after {HEALTH_TIMEOUT_S} s. Last logs:\n"
            st.error = msg + out[-(MAX_LOG_CHARS - len(msg)) :]
    status = "healthy" if all(st.healthy for st in states) else "failed"
    return await _save(db, project_id, SandboxState(status=status, services=states))


async def start(db: Db, project: Project, files: list[CodeFile], spec: SandboxSpec) -> SandboxState:
    """Stop the old sandbox, rewrite the work dir, run every service and wait for health.

    Never raises for an unusable sandbox: returns status "failed" with the reason.
    """
    async with _lock(project.id):
        return await _start(db, project, files, spec)


async def _start(
    db: Db, project: Project, files: list[CodeFile], spec: SandboxSpec
) -> SandboxState:
    pid = str(project.id)
    if missing := missing_dirs(spec, files):
        return await _save(db, project.id, _failed(spec, f"No such folder: {', '.join(missing)}"))
    if _fake():
        services = [ServiceState(name=s.name, url=FAKE_URL, healthy=True) for s in spec.services]
        state = SandboxState(status="healthy", services=services)
        return await _save(db, project.id, state, spec=spec.model_dump())
    if error := _unavailable():
        return await _save(db, project.id, _failed(spec, error))
    doc = await db.sandboxes.find_one({"_id": project.id})
    if error := await _remove(pid, _names(pid, doc) | _names(pid, {"spec": spec.model_dump()})):
        return await _save(db, project.id, _failed(spec, error))
    try:
        _write_files(_workdir(pid), files, (doc or {}).get("files", []))
    except (ValueError, OSError) as exc:
        return await _save(db, project.id, _failed(spec, f"Cannot write the app files: {exc}"))
    await _save(
        db, project.id, SandboxState(status="starting"),
        spec=spec.model_dump(), files=[f.path for f in files],
    )  # fmt: skip
    return await _launch(db, project.id, spec)


async def restart(db: Db, project: Project) -> SandboxState:
    """Run the last started spec again from the files already in the work dir."""
    async with _lock(project.id):
        return await _restart(db, project)


async def _restart(db: Db, project: Project) -> SandboxState:
    doc = await db.sandboxes.find_one({"_id": project.id})
    if not doc or not doc.get("spec"):
        raise Conflict("No sandbox has been started for this project yet")
    spec = SandboxSpec.model_validate(doc["spec"])
    if _fake():
        services = [ServiceState(name=s.name, url=FAKE_URL, healthy=True) for s in spec.services]
        return await _save(db, project.id, SandboxState(status="healthy", services=services))
    if error := _unavailable():
        return await _save(db, project.id, _failed(spec, error))
    if error := await _remove(str(project.id), _names(str(project.id), doc)):
        return await _save(db, project.id, _failed(spec, error))
    return await _launch(db, project.id, spec)


async def stop(project_id: uuid.UUID) -> SandboxState:
    async with _lock(project_id):
        db = database()
        doc = await db.sandboxes.find_one({"_id": project_id})
        if doc is None:
            return SandboxState(status="stopped")
        if not _fake() and _unavailable() is None:
            if error := await _remove(str(project_id), _names(str(project_id), doc)):
                services = [
                    ServiceState(name=s["name"], error=error) for s in doc.get("services", [])
                ]
                return await _save(db, project_id, SandboxState(status="failed", services=services))
        return await _save(db, project_id, SandboxState(status="stopped"))


async def status(project_id: uuid.UUID) -> SandboxState:
    db = database()
    doc = await db.sandboxes.find_one({"_id": project_id})
    if doc is None:
        return SandboxState(status="stopped")
    state = SandboxState.model_validate(doc)
    if state.status != "healthy" or _fake() or _unavailable() is not None:
        return state
    running = 0
    for st in state.services:
        name = docker.container(str(project_id), st.name)
        rc, out = await docker.exec("docker", "inspect", "-f", "{{.State.Running}}", name)
        if rc == 0 and out.strip() == "true":
            running += 1
        else:
            st.healthy, st.error = False, "The container is no longer running"
    if running == len(state.services):
        return state
    state.status = "failed" if running else "stopped"
    await db.sandboxes.update_one({"_id": project_id}, {"$set": state.model_dump()})
    return state


async def logs(project_id: uuid.UUID, svc: str, tail: int = 200) -> str:
    doc = await database().sandboxes.find_one({"_id": project_id})
    if svc not in {s["name"] for s in (doc or {}).get("spec", {}).get("services", [])}:
        raise NotFound("Service not found")
    if _fake() or _unavailable() is not None:
        return ""
    _, out = await docker.exec(
        "docker", "logs", "--tail", str(tail), docker.container(str(project_id), svc)
    )
    return out


async def touch(project_id: uuid.UUID) -> None:
    await database().sandboxes.update_one({"_id": project_id}, {"$set": {"last_seen": now()}})


async def reap_idle() -> None:
    """Stop sandboxes nobody has looked at for 30 minutes."""
    query = {"status": {"$ne": "stopped"}, "last_seen": {"$lt": now() - IDLE_STOP}}
    async for doc in database().sandboxes.find(query, {"_id": 1}):
        try:
            await stop(doc["_id"])
        except Exception:  # one broken sandbox must not block reaping the others
            logger.exception("Cannot stop idle sandbox %s", doc["_id"])
