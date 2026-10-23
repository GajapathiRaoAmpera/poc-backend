import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from app.core.deps import CurrentUser, DbSession
from app.modules.artifacts.models import ValidationIssue
from app.modules.changesets import service
from app.modules.changesets.models import ChangeSet
from app.modules.diagrams.diff import Impact
from app.modules.orchestration.schemas import RevisionOut, revision_out
from app.modules.projects.service import get_project_for

router = APIRouter(prefix="/projects/{project_id}", tags=["change-sets"])


class ChangeSetOut(BaseModel):
    id: uuid.UUID
    base_revision: int
    run_id: uuid.UUID | None
    instruction: str | None
    status: str
    ops: list[dict[str, Any]]
    summary: str
    impact: Impact
    issues: list[ValidationIssue]
    ok: bool
    applied_revision: int | None
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def of(cls, cs: ChangeSet) -> "ChangeSetOut":
        return cls(
            id=cs.id,
            base_revision=cs.base_revision,
            run_id=cs.run_id,
            instruction=cs.instruction,
            status=cs.status,
            ops=cs.ops,
            summary=cs.summary,
            impact=cs.impact,
            issues=cs.validation.issues,
            ok=cs.validation.ok,
            applied_revision=cs.applied_revision,
            created_at=cs.created_at,
            decided_at=cs.decided_at,
        )


@router.get("/change-sets")
async def list_change_sets(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    status_: Annotated[
        Literal["PROPOSED", "APPLIED", "REJECTED", "INVALID", "SUPERSEDED"] | None,
        Query(alias="status"),
    ] = None,
) -> list[ChangeSetOut]:
    project = await get_project_for(db, user, project_id)
    query: dict = {"project_id": project.id}
    if status_:
        query["status"] = status_
    cursor = db.change_sets.find(query).sort("created_at", -1).limit(50)
    return [ChangeSetOut.of(ChangeSet.from_doc(d)) async for d in cursor]


@router.get("/change-sets/{change_set_id}")
async def get_change_set(
    project_id: uuid.UUID, change_set_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ChangeSetOut:
    project = await get_project_for(db, user, project_id)
    return ChangeSetOut.of(await service.get_change_set(db, project, change_set_id))


@router.post("/change-sets/{change_set_id}/apply", status_code=status.HTTP_201_CREATED)
async def apply(
    project_id: uuid.UUID, change_set_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> RevisionOut:
    project = await get_project_for(db, user, project_id)
    return revision_out(await service.apply_change_set(db, project, change_set_id, user))


@router.post("/change-sets/{change_set_id}/reject")
async def reject(
    project_id: uuid.UUID, change_set_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ChangeSetOut:
    project = await get_project_for(db, user, project_id)
    return ChangeSetOut.of(await service.reject_change_set(db, project, change_set_id, user))
