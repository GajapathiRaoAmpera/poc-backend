import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints

from app.modules.auth.schemas import _Strict
from app.modules.conversations.models import Message
from app.modules.orchestration.schemas import RunOut

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class SendIn(_Strict):
    text: Text


class MessageOut(BaseModel):
    id: uuid.UUID
    channel: str
    seq: int
    role: str
    agent: str | None
    text: str
    refs: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, m: Message) -> "MessageOut":
        return cls.model_validate(m.model_dump())


class SendOut(BaseModel):
    message: MessageOut
    run: RunOut | None = None
