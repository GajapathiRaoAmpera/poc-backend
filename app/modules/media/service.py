"""Small binary store (PNG screenshots) scoped to a project."""

import uuid

from bson import Binary

from app.core.db import Db
from app.core.documents import now
from app.core.errors import AppError, NotFound
from app.modules.projects.models import Project

MAX_BYTES = 2 * 1024 * 1024


async def put(db: Db, project: Project, data: bytes, content_type: str) -> str:
    if len(data) > MAX_BYTES:
        raise AppError("MEDIA_TOO_LARGE", "Media is larger than 2 MB", 413)
    media_id = uuid.uuid4()
    await db.media.insert_one(
        {
            "_id": media_id,
            "project_id": project.id,
            "content_type": content_type,
            "data": Binary(data),
            "created_at": now(),
        }
    )
    return str(media_id)


async def get(db: Db, project: Project, media_id: str | uuid.UUID) -> tuple[bytes, str]:
    try:
        mid = uuid.UUID(str(media_id))
    except ValueError:
        raise NotFound("Media not found") from None
    doc = await db.media.find_one({"_id": mid, "project_id": project.id})
    if doc is None:
        raise NotFound("Media not found")
    return bytes(doc["data"]), doc["content_type"]
