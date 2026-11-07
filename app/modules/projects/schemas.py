import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, StringConstraints

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


REPO_URL_RE = re.compile(r"^https://[\w.-]+/[\w./-]+?(\.git)?$")


def check_repo_url(url: str) -> str:
    if not REPO_URL_RE.match(url) or ".." in url:
        raise ValueError("Use an https:// repository URL, e.g. https://github.com/acme/app.git")
    return url


class RepoIn(_Strict):
    url: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=300),
        AfterValidator(check_repo_url),
    ]
    token: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
        | None
    ) = None


class RepoOut(BaseModel):
    url: str | None
    token_set: bool
    token_last4: str | None


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: ProjectStatus
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    completeness: float
    name_source: str = "user"
    preferences: dict[str, Any] | None = None


class SpecOut(BaseModel):
    revision: int
    # Stored JSON exactly as validated on save (None fields omitted).
    data: dict[str, Any]
    created_at: datetime | None
    created_by: uuid.UUID | None
