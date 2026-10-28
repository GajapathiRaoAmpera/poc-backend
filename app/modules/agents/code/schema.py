from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Path = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Content = Annotated[str, StringConstraints(max_length=20000)]
Step = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeFile(_M):
    path: Path
    content: Content
    executable: bool = False
    # Written by the platform (PLAN.md), never by the LLM.
    generated: bool = False


class CodeAgentOutput(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    project_slug: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,39}$")]
    files: Annotated[list[CodeFile], Field(min_length=1, max_length=61)]
    next_steps: Annotated[list[Step], Field(max_length=10)] = []


class FileChange(_M):
    op: Literal["write", "delete"]
    path: Path
    content: Content | None = None
    executable: bool = False


class CodeChanges(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    changes: Annotated[list[FileChange], Field(min_length=1, max_length=30)]


class Finding(_M):
    tool: Literal["bash-n", "shellcheck", "manifest"]
    file: str  # "<script>" or a manifest path
    line: int | None = None
    code: str | None = None
    level: Literal["error", "warning"] = "error"
    message: str


class Report(_M):
    bash_n: Literal["ok", "failed"]
    shellcheck: Literal["ok", "failed", "unavailable"]
    shellcheck_version: str | None = None
    findings: list[Finding] = []
    checked_at: datetime

    @property
    def ok(self) -> bool:
        return not self.findings


class CodeContent(CodeAgentOutput):
    report: Report
    script_sha256: str
