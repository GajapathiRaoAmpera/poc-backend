import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body

from app.core.deps import CurrentUser, DbSession, SuperAdmin
from app.modules.projects.service import get_project_for
from app.modules.ratecard import service
from app.modules.ratecard.models import RateCard

admin_router = APIRouter(prefix="/admin/rate-card", tags=["admin"])
router = APIRouter(prefix="/projects/{project_id}/rate-card", tags=["projects"])


@admin_router.get("")
async def get_default(admin: SuperAdmin, db: DbSession) -> dict[str, Any]:
    doc = await service.default_doc(db)
    card = RateCard.model_validate(doc).model_dump(mode="json")
    return {**card, "placeholder": doc["updated_by"] is None}


@admin_router.put("")
async def put_default(card: RateCard, admin: SuperAdmin, db: DbSession) -> RateCard:
    return await service.save_default(db, admin, card)


async def _out(db: DbSession, project) -> dict[str, Any]:
    doc = await service.default_doc(db)
    return {
        "effective": await service.effective(db, project),
        "override": RateCard.model_validate(project.rate_card) if project.rate_card else None,
        "source": "project" if project.rate_card else "platform",
        "placeholder": doc["updated_by"] is None,
    }


@router.get("")
async def get_project_card(project_id: uuid.UUID, user: CurrentUser, db: DbSession):
    return await _out(db, await get_project_for(db, user, project_id))


@router.put("")
async def put_project_card(
    project_id: uuid.UUID,
    card: Annotated[RateCard | None, Body()],
    user: CurrentUser,
    db: DbSession,
):
    project = await get_project_for(db, user, project_id)
    await service.save_override(db, project, user, card)
    return await _out(db, project)
