"""Manifest rules (spec §4): files stay inside the target directory and carry no secrets."""

import re

from app.modules.agents.code.schema import CodeAgentOutput, CodeFile, FileChange
from app.modules.artifacts.models import ValidationIssue

MAX_FILES = 60
MAX_TOTAL = 300_000
PLAN_FILE = "PLAN.md"
_PATH = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
_SECRET = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}")


def _err(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity="error", code=code, message=message, path=path)


def _forbidden(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        name == ".env"
        or name.endswith(".pem")
        or name.startswith("id_rsa")
        or path.startswith(".ssh/")
        or "/.ssh/" in path
    )


def path_issue(path: str) -> ValidationIssue | None:
    parts = path.split("/")
    if not _PATH.match(path) or any(p in (".", "..") or p.startswith(".git") for p in parts):
        return _err(
            "PATH",
            f"{path}: use a relative path of letters, digits, '.', '_', '-' and '/' "
            "(no '..', no .git)",
            path,
        )
    if _forbidden(path):
        return _err("FORBIDDEN", f"{path}: secret files are not allowed (use .env.example)", path)
    return None


def manifest_issues(out: CodeAgentOutput, allow_generated: bool = False) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    agent_files = [f for f in out.files if not f.generated]
    if len(agent_files) > MAX_FILES:
        issues.append(_err("TOO_LARGE", f"At most {MAX_FILES} files (got {len(agent_files)})"))
    seen: dict[str, str] = {}
    for f in out.files:
        if (issue := path_issue(f.path)) is not None:
            issues.append(issue)
            continue
        if f.path == PLAN_FILE and not (allow_generated and f.generated):
            issues.append(_err("RESERVED", "PLAN.md is generated from the plan; do not write it"))
        if (key := f.path.lower()) in seen:
            issues.append(_err("DUPLICATE", f"{f.path}: duplicate of {seen[key]}", f.path))
        seen[key] = f.path
        if "\x00" in f.content:
            issues.append(_err("BINARY", f"{f.path}: text files only", f.path))
        for n, line in enumerate(f.content.splitlines(), 1):
            if _SECRET.search(line):
                issues.append(
                    _err("SECRET", f"{f.path}:{n}: looks like a secret key; use a placeholder")
                )
    dirs = {"/".join(p.split("/")[:i]) for p in seen for i in range(1, p.count("/") + 1)}
    for key in sorted(seen.keys() & dirs):
        issues.append(_err("PATH", f"{seen[key]} is both a file and a directory", seen[key]))
    total = sum(len(f.content) for f in out.files)
    if total > MAX_TOTAL:
        issues.append(_err("TOO_LARGE", f"Total content is {total} characters (max {MAX_TOTAL})"))
    return issues


def _norm(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text if not text or text.endswith("\n") else text + "\n"


def normalise(out: CodeAgentOutput) -> CodeAgentOutput:
    files = [f.model_copy(update={"content": _norm(f.content)}) for f in out.files]
    return out.model_copy(update={"files": files})


def apply_changes(files: list[CodeFile], changes: list[FileChange]) -> list[CodeFile]:
    by_path = {f.path: f for f in files}
    for c in changes:
        current = by_path.get(c.path)
        if current is not None and current.generated:
            raise ValueError(f"{c.path} is generated from the plan and cannot be changed")
        if c.op == "delete":
            if current is None:
                raise ValueError(f"Cannot delete {c.path}: no such file")
            del by_path[c.path]
        else:
            if c.content is None:
                raise ValueError(f"Write to {c.path} needs content")
            by_path[c.path] = CodeFile(
                path=c.path, content=_norm(c.content), executable=c.executable
            )
    return list(by_path.values())


def plan_md(plan: dict) -> CodeFile:
    lines = ["# Delivery plan", "", "Generated from the approved plan. Tick tasks as you go.", ""]
    for m in plan.get("milestones", []):
        lines += [f"## {m['id']} {m['title']}", ""]
        for t in (t for t in plan.get("tasks", []) if t["milestone_id"] == m["id"]):
            lines.append(f"- [ ] {t['id']} {t['title']} ({t['role']}, {t['effort_days']:g}d)")
        lines.append("")
    return CodeFile(path=PLAN_FILE, content="\n".join(lines), generated=True)
