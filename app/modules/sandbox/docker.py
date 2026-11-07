"""Thin docker CLI wrapper. argv only (never a shell on the host); tests replace `exec`."""

import asyncio
import shlex
from pathlib import Path

from app.modules.sandbox.spec import IMAGES, SandboxService

# Lets the uid-1000 user install into /app (pip --user / npm) and run what it installed.
_PATH = (
    "/app/.local/bin:/app/node_modules/.bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


async def exec(*argv: str) -> tuple[int, str]:  # noqa: A001
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:
        return 127, str(exc)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


def network(project_id: str) -> str:
    return f"ampera-{project_id}"


def container(project_id: str, svc: str) -> str:
    return f"ampera-{project_id}-{svc}"


def label(project_id: str) -> str:
    return f"ampera.project={project_id}"


def run_args(project_id: str, svc: SandboxService, workdir: Path) -> list[str]:
    # The only shell is inside the container, and every model-supplied word is shlex-quoted.
    command = shlex.join(svc.install) + " && exec " + shlex.join(svc.start)
    return [
        "docker", "run", "-d",
        "--name", container(project_id, svc.name),
        "--network", network(project_id),
        "--label", label(project_id),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "1g",
        "--cpus", "1",
        "--pids-limit", "256",
        "--user", "1000:1000",
        "-e", "HOME=/app",
        "-e", f"PATH={_PATH}",
        "-v", f"{workdir / svc.dir}:/app",
        "-w", "/app",
        "-p", f"127.0.0.1::{svc.port}",
        IMAGES[svc.runtime],
        "sh", "-c", command,
    ]  # fmt: skip
