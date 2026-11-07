"""Push files as a new branch to the user's repo. argv only; the token goes via GIT_ASKPASS."""

import asyncio
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from app.core.errors import AppError
from app.modules.agents.code.schema import CodeFile

TIMEOUT_S = 120
AUTHOR = ["-c", "user.name=Ampera", "-c", "user.email=noreply@amperatech.ai"]
# Prints the username for git's "Username for ..." prompt, the token for "Password for ...".
ASKPASS = """#!/bin/sh
case "$1" in
  Username*) printf '%s\\n' x-access-token ;;
  *) printf '%s\\n' "$AMPERA_GIT_TOKEN" ;;
esac
"""


def scrub(text: str, token: str) -> str:
    for secret in {token, quote(token, safe=""), quote(token)} - {""}:
        text = text.replace(secret, "***")
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)


def write_askpass(folder: Path) -> Path:
    path = folder / "askpass.sh"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, "w") as f:
        f.write(ASKPASS)
    return path


def _write_files(work: Path, files: list[CodeFile]) -> None:
    for f in files:
        parts = PurePosixPath(f.path).parts
        if f.path.startswith("/") or ".." in parts or ".git" in parts:
            raise AppError("UNSAFE_PATH", f"Refusing to write {f.path[:200]}", 422)
        target = work.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        if f.executable:
            target.chmod(0o755)


# Every call: no host/global config, no hooks, no credential helper that could store the token.
ISOLATE = ["-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]


def _env(root: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal allowlisted env: nothing from the host leaks in except PATH."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https:file",  # file: local bare repos in tests
        **(extra or {}),
    }


async def _git(root: Path, *args: str, extra: dict[str, str] | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *ISOLATE, *args, cwd=root / "work", env=_env(root, extra),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except TimeoutError:
        await _kill(proc)
        return 124, "git timed out"
    except BaseException:  # cancelled run: git must be dead before the temp dir goes
        await asyncio.shield(_kill(proc))
        raise
    return proc.returncode or 0, out.decode(errors="replace")


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.kill()
        await proc.wait()


async def _ok(root: Path, *args: str) -> str:
    code, out = await _git(root, *args)
    if code:
        raise AppError("GIT_FAILED", f"A git step failed: {out[-300:]}", 500)
    return out.strip()


async def push(
    files: list[CodeFile],
    repo_url: str,
    token: str,
    branch: str,
    message: str,
    on_commit: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """Commit ``files`` in a fresh repo and push it to ``refs/heads/<branch>``; returns the sha."""
    root = Path(tempfile.mkdtemp(prefix="ampera-deploy-"))  # 0700
    try:
        work = root / "work"
        work.mkdir()
        _write_files(work, files)
        await _ok(root, "init", "-q", "-b", "main")
        await _ok(root, "add", "-A")
        await _ok(root, *AUTHOR, "commit", "-q", "--no-verify", "-m", message)
        sha = await _ok(root, "rev-parse", "HEAD")
        if on_commit:
            await on_commit()
        extra = {"GIT_ASKPASS": str(write_askpass(root)), "AMPERA_GIT_TOKEN": token}
        code, out = await _git(
            root, "push", "--porcelain", repo_url, f"HEAD:refs/heads/{branch}", extra=extra
        )
        if code:
            out = scrub(out, token)
            if "[rejected]" in out or "already exists" in out:
                raise AppError(
                    "BRANCH_EXISTS",
                    f"Branch {branch} already exists in the repository and was not overwritten. "
                    "Delete that branch in your repository, or approve a new implementation "
                    "revision, then push again.",
                    409,
                )
            raise AppError("PUSH_FAILED", f"The push was rejected: {out[-500:].strip()}", 502)
        return sha
    finally:
        shutil.rmtree(root, ignore_errors=True)
