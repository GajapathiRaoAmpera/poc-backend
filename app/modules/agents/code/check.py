"""Static validation (spec §6): ``bash -n`` and shellcheck read the script from stdin.
Nothing generated is ever executed."""

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

from app.core.documents import now
from app.modules.agents.code.schema import CodeFile, Finding, Report

SCRIPT = "<script>"
TIMEOUT_S = 20
_SHEBANG = re.compile(r"^#!\s*/\S*?(?:env\s+)?(bash|sh|dash|ksh)\b")


def shellcheck_path() -> str | None:
    # shellcheck-py installs the binary next to the interpreter, which may not be on PATH.
    local = Path(sys.executable).parent / "shellcheck"
    return shutil.which("shellcheck") or (str(local) if local.exists() else None)


async def _exec(*args: str, stdin: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def dialect(f: CodeFile) -> str | None:
    """The shell a manifest file is written for, or None when it is not a shell script."""
    m = _SHEBANG.match(f.content.split("\n", 1)[0])
    if m:
        return "bash" if m.group(1) == "bash" else "sh"
    return "bash" if f.path.endswith(".sh") else None


async def _bash_n(script: str) -> list[Finding]:
    bash = shutil.which("bash") or "/bin/bash"
    code, _, err = await _exec(bash, "-n", stdin=script)
    if code == 0:
        return []
    found = []
    for line in err.strip().splitlines()[:10]:
        m = re.search(r"line (\d+): (.*)", line)
        found.append(
            Finding(
                tool="bash-n",
                file=SCRIPT,
                line=int(m.group(1)) if m else None,
                message=(m.group(2) if m else line)[:300],
            )
        )
    return found or [Finding(tool="bash-n", file=SCRIPT, message="Syntax check failed")]


async def _shellcheck(binary: str, text: str, shell: str, file: str) -> list[Finding]:
    _, out, err = await _exec(binary, "-f", "json1", "-S", "warning", "-s", shell, "-", stdin=text)
    try:
        comments = json.loads(out)["comments"]
    except (ValueError, KeyError) as exc:
        raise RuntimeError(err.strip() or "shellcheck returned no report") from exc
    return [
        Finding(
            tool="shellcheck",
            file=file,
            line=c["line"],
            code=f"SC{c['code']}",
            level="error",  # warning-level findings also block approval (spec §6)
            message=c["message"][:300],
        )
        for c in comments
    ]


async def version() -> str | None:
    binary = shellcheck_path()
    if binary is None:
        return None
    _, out, _ = await _exec(binary, "--version", stdin="")
    m = re.search(r"version: (\S+)", out)
    return m.group(1) if m else None


async def check(script: str, files: list[CodeFile]) -> Report:
    findings = await _bash_n(script)
    bash_n = "failed" if findings else "ok"
    binary = shellcheck_path()
    sc_status, sc_version = "unavailable", None
    if binary is not None:
        try:
            sc = await _shellcheck(binary, script, "bash", SCRIPT)
            for f in sorted(files, key=lambda f: f.path):
                if (shell := dialect(f)) is not None:
                    sc += await _shellcheck(binary, f.content, shell, f.path)
            sc_version = await version()
            sc_status = "failed" if sc else "ok"
            findings += sc
        except (TimeoutError, RuntimeError, OSError) as exc:
            binary, reason = None, str(exc) or "timed out"
    else:
        reason = "shellcheck is not installed"
    if binary is None:
        findings.append(
            Finding(
                tool="shellcheck",
                file=SCRIPT,
                code="VALIDATOR_UNAVAILABLE",
                message=f"Validation could not run: {reason}",
            )
        )
    return Report(
        bash_n=bash_n,
        shellcheck=sc_status,
        shellcheck_version=sc_version,
        findings=findings,
        checked_at=now(),
    )
