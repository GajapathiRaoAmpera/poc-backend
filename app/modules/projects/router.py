import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import CurrentUser, DbSession
from app.core.pagination import Page, PageParams
from app.modules.projects import service
from app.modules.projects.models import ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectOut, ProjectUpdate

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
