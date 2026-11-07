from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeployMessage(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class DeployContent(_M):
    repo_url: str
    branch: str
    commit_sha: str
    files: list[str]
    pushed_at: datetime
    commit_message: str
