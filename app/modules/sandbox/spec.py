"""sandbox.json (spec §5.1): services to run, validated; commands are argv lists, never shell."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.agents.code.schema import CodeFile
from app.modules.agents.code.validate import path_issue

IMAGES = {"python3.12": "python:3.12-slim", "node22": "node:22-slim"}

Arg = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Argv = Annotated[list[Arg], Field(min_length=1, max_length=40)]


class SandboxService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,20}$")]
    dir: Annotated[str, StringConstraints(max_length=200)]
    runtime: Literal["python3.12", "node22"]
    install: Argv
    start: Argv
    port: Annotated[int, Field(ge=1024, le=65535)]
    health: Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9._~/-]{0,200}$")] = "/health"

    @field_validator("dir")
    @classmethod
    def _dir(cls, v: str) -> str:
        # Same rules as manifest paths: relative, no '..', no ':' (it would split the -v mount).
        if (issue := path_issue(v)) is not None:
            raise ValueError(issue.message)
        return v


class SandboxSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    services: Annotated[list[SandboxService], Field(min_length=1, max_length=4)]

    @field_validator("services")
    @classmethod
    def _unique(cls, v: list[SandboxService]) -> list[SandboxService]:
        if len({s.name for s in v}) != len(v):
            raise ValueError("service names must be unique")
        # Nested dirs would let one container plant a symlink that Docker follows as another
        # container's bind source (a host mount escape).
        dirs = [s.dir for s in v]
        for i, a in enumerate(dirs):
            for b in dirs[i + 1 :]:
                if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                    raise ValueError(f"service folders must not overlap: {a!r} and {b!r}")
        return v


def missing_dirs(spec: SandboxSpec, files: list[CodeFile]) -> list[str]:
    """Service dirs that are not a folder of the manifest."""
    return [s.dir for s in spec.services if not any(f.path.startswith(s.dir + "/") for f in files)]
