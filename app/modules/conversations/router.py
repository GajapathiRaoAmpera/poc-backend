import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession
from app.core.errors import Conflict, StageBlocked
from app.modules.conversations import service
from app.modules.conversations.models import Channel
from app.modules.conversations.schemas import MessageOut, SendIn, SendOut
from app.modules.delivery.service import notify
from app.modules.orchestration.schemas import RunOut
from app.modules.orchestration.service import start_stage
from app.modules.orchestration.stages import dependency_blockers
from app.modules.projects.service import get_project_for

router = APIRouter(prefix="/projects/{project_id}/conversations", tags=["conversations"])

AGENT_LABEL = {
    "requirements": "Requirements Agent",
    "architecture": "Architecture Agent",
    "plan": "Plan Agent",
    "budget": "Budget Agent",
}


@router.get("/{channel}/messages")
async def list_messages(
    project_id: uuid.UUID,
    channel: Channel,
    user: CurrentUser,
    db: DbSession,
    after: Annotated[int, Query(ge=0)] = 0,
) -> list[MessageOut]:
    project = await get_project_for(db, user, project_id)
    return [MessageOut.of(m) for m in await service.list_messages(db, project, channel, after)]


@router.post("/{channel}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    project_id: uuid.UUID, channel: Channel, body: SendIn, user: CurrentUser, db: DbSession
) -> SendOut:
    project = await get_project_for(db, user, project_id)
    if channel == "intake":
        msg = await service.add_message(db, project, "intake", "user", body.text)
        await notify(db, project, "user_message", body.text)
        return SendOut(message=MessageOut.of(msg))
    if await db.agent_runs.count_documents(
        {"project_id": project.id, "stage": channel, "active": True}, limit=1
    ):
        raise Conflict(f"The {AGENT_LABEL[channel]} is busy. Send your message when it finishes.")
    blockers = await dependency_blockers(db, project, channel)
    msg = await service.add_message(db, project, channel, "user", body.text)
    if blockers:
        await service.add_message(db, project, channel, "system", blockers[0])
        raise StageBlocked(blockers)
    run, _ = await start_stage(db, project, channel, "chat", user, instruction=body.text)
    return SendOut(message=MessageOut.of(msg), run=RunOut.of(run))
