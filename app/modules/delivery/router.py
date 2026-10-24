import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFound
from app.modules.conversations.service import add_message
from app.modules.delivery import plans, service
from app.modules.delivery.schemas import DeliveryOut, IntakeIn, IntakeOut, PlanOut
from app.modules.delivery.threads import get_thread
from app.modules.projects.models import Project
from app.modules.projects.service import get_project_for, to_out

router = APIRouter(prefix="/projects", tags=["delivery"])

PLACEHOLDER_LEN = 60


def _placeholder_name(message: str) -> str:
    first = " ".join(message.split())
    return first if len(first) <= PLACEHOLDER_LEN else first[:PLACEHOLDER_LEN].rstrip() + "…"


async def _delivery(db, project: Project) -> DeliveryOut:
    return DeliveryOut.of(await get_thread(db, project), await plans.current_plan(db, project))


@router.post("/intake", status_code=status.HTTP_201_CREATED)
async def intake(body: IntakeIn, user: CurrentUser, db: DbSession) -> IntakeOut:
    project = Project(
        owner_id=user.id,
        name=_placeholder_name(body.message),
        name_source="intake",
        description=body.message[:5000],
        preferences=body.preferences.model_dump(mode="json", exclude_none=True)
        if body.preferences
        else None,
    )
    await db.projects.insert_one(project.to_doc())
    await add_message(db, project, "intake", "user", body.message)
    await service.start_delivery(db, project, user)
    return IntakeOut(project=to_out(project, None), delivery=await _delivery(db, project))


@router.get("/{project_id}/delivery")
async def get_delivery(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DeliveryOut:
    return await _delivery(db, await get_project_for(db, user, project_id))


@router.post("/{project_id}/delivery/resume")
async def resume(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DeliveryOut:
    project = await get_project_for(db, user, project_id)
    await service.resume(db, project, user)
    return await _delivery(db, project)


@router.post("/{project_id}/delivery/cancel")
async def cancel(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DeliveryOut:
    project = await get_project_for(db, user, project_id)
    await service.cancel(db, project, user)
    return await _delivery(db, project)


@router.get("/{project_id}/plans")
async def list_plans(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[PlanOut]:
    project = await get_project_for(db, user, project_id)
    return [PlanOut.of(p) for p in await plans.list_plans(db, project)]


@router.get("/{project_id}/plans/{version}")
async def get_plan(
    project_id: uuid.UUID, version: int, user: CurrentUser, db: DbSession
) -> PlanOut:
    project = await get_project_for(db, user, project_id)
    plan = await plans.get_plan(db, project, version)
    if plan is None:
        raise NotFound("Plan not found")
    return PlanOut.of(plan)
