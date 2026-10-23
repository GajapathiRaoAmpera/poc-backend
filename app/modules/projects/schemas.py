import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.modules.auth.schemas import _Strict
from app.modules.projects.models import ProjectStatus

ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]


class ProjectCreate(_Strict):
    name: ProjectName
    description: Description | None = None


class ProjectUpdate(_Strict):
    name: ProjectName | None = None
    description: Description | None = None
    status: ProjectStatus | None = None


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: ProjectStatus
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    completeness: float
