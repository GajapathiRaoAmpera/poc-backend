from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.agents.code.schema import CodeFile
from app.modules.sandbox.spec import SandboxSpec


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(_M):
    service: str
    healthy: bool
    log_excerpt: Annotated[str, StringConstraints(max_length=4000)] = ""


class ImplementationContent(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    files: Annotated[list[CodeFile], Field(min_length=1)]
    sandbox: SandboxSpec
    health: list[Health] = []
    components_done: list[str] = []
