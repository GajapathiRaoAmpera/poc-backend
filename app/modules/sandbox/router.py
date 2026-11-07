import uuid

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.modules.projects.service import get_project_for
from app.modules.sandbox import service
from app.modules.sandbox.service import SandboxState

router = APIRouter(prefix="/projects/{project_id}/sandbox", tags=["sandbox"])


@router.get("")
async def get_sandbox(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SandboxState:
    project = await get_project_for(db, user, project_id)
    await service.touch(project.id)
    return await service.status(project.id)


@router.post("/restart")
async def restart_sandbox(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SandboxState:
    project = await get_project_for(db, user, project_id)
    return await service.restart(db, project)


@router.post("/stop")
async def stop_sandbox(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SandboxState:
    project = await get_project_for(db, user, project_id)
    return await service.stop(project.id)


@router.get("/logs/{svc}")
async def sandbox_logs(
    project_id: uuid.UUID, svc: str, user: CurrentUser, db: DbSession
) -> dict[str, str]:
    project = await get_project_for(db, user, project_id)
    await service.touch(project.id)
    return {"logs": await service.logs(project.id, svc)}
