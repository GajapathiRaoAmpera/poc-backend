from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.modules.diagrams.ops import Op
from app.modules.diagrams.schema import ArchitectureContent

Summary = Annotated[str, StringConstraints(max_length=500)]


class ArchitectureAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: ArchitectureContent
    summary: Summary


class ChangeSetAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ops: list[Op]
    summary: Summary
