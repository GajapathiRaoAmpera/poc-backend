import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import CurrentUser, DbSession
from app.core.pagination import Page, PageParams
from app.modules.delivery.service import notify
from app.modules.projects import service
from app.modules.projects.models import ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectOut, ProjectUpdate, SpecOut
from app.modules.projects.spec_schema import Constraints, ProjectSpecification

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectOut])
async def list_projects(
    user: CurrentUser,
    db: DbSession,
    params: PageParams,
    q: str | None = None,
    status: ProjectStatus | None = None,
) -> Page[ProjectOut]:
    items, total = await service.list_projects(db, user, params, q, status)
    return Page(items=items, page=params.page, page_size=params.page_size, total=total)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(data: ProjectCreate, user: CurrentUser, db: DbSession) -> ProjectOut:
    return await service.create_project(db, user, data)


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProjectOut:
    return await service.get_project(db, user, project_id)


@router.patch("/{project_id}")
async def update_project(
    project_id: uuid.UUID, data: ProjectUpdate, user: CurrentUser, db: DbSession
) -> ProjectOut:
    return await service.update_project(db, user, project_id, data)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    await service.delete_project(db, user, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/specification")
async def get_specification(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SpecOut:
    return await service.get_specification(db, user, project_id)


@router.put("/{project_id}/specification", status_code=status.HTTP_201_CREATED)
async def save_specification(
    project_id: uuid.UUID, data: ProjectSpecification, user: CurrentUser, db: DbSession
) -> SpecOut:
    out = await service.save_specification(db, user, project_id, data)
    # A manual brief edit is authoritative: no re-plan (the orchestrator would rewrite the brief);
    # requirements built on the old brief are stale and the router redoes them.
    await notify(db, await service.get_project_for(db, user, project_id), "edited:specification")
    return out


@router.put("/{project_id}/preferences")
async def save_preferences(
    project_id: uuid.UUID, data: Constraints, user: CurrentUser, db: DbSession
) -> ProjectOut:
    """Optional preferences; they override the Orchestrator's brief on its next plan."""
    out = await service.save_preferences(
        db, user, project_id, data.model_dump(mode="json", exclude_none=True)
    )
    await notify(db, await service.get_project_for(db, user, project_id), "user_message")
    return out
