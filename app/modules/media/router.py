import uuid

from fastapi import APIRouter, Response

from app.core.deps import CurrentUser, DbSession
from app.modules.media import service
from app.modules.projects.service import get_project_for

router = APIRouter(prefix="/projects/{project_id}/media", tags=["media"])


@router.get("/{media_id}")
async def get_media(project_id: uuid.UUID, media_id: str, user: CurrentUser, db: DbSession):
    project = await get_project_for(db, user, project_id)
    data, content_type = await service.get(db, project, media_id)
    return Response(
        data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
