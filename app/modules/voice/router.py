import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.documents import now
from app.core.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.auth.schemas import _Strict
from app.modules.conversations.models import Channel
from app.modules.projects.service import get_project_for
from app.modules.voice import service

router = APIRouter(prefix="/projects/{project_id}/voice", tags=["voice"])


class SessionIn(_Strict):
    channel: Channel


class SessionOut(BaseModel):
    token: str
    model: str
    expires_at: datetime
    tool: str
    agent: str
    voice: str


@router.post("/session", status_code=status.HTTP_201_CREATED)
async def create_session(
    project_id: uuid.UUID, body: SessionIn, user: CurrentUser, db: DbSession
) -> SessionOut:
    project = await get_project_for(db, user, project_id)
    settings = get_settings()
    used = await db.audit_logs.count_documents(
        {
            "actor_id": user.id,
            "action": "voice.session",
            "created_at": {"$gte": now() - timedelta(days=1)},
        }
    )
    if used >= settings.voice_daily_session_limit:
        raise AppError(
            "QUOTA_EXCEEDED",
            f"Daily limit of {settings.voice_daily_session_limit} voice sessions reached. "
            "Type your message instead, or try again tomorrow.",
            429,
        )
    out = await service.mint_token(body.channel, settings)
    await record_audit(
        db, user.id, "voice.session", "project", project.id, {"channel": body.channel}
    )
    return SessionOut(**out)
