import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFound, RevisionConflict, StageBlocked
from app.modules.agents.requirements.service import save_answers
from app.modules.artifacts import service
from app.modules.artifacts.models import SourceRef, ValidationIssue
from app.modules.diagrams.diff import DiagramDiff, diff
from app.modules.diagrams.schema import ArchitectureContent
from app.modules.orchestration import service as orchestration
from app.modules.orchestration.schemas import (
    ApprovalOut,
    ArtifactOut,
    RevisionOut,
    RevisionSummaryOut,
    RunOut,
    approval_out,
    artifact_out,
    revision_out,
    revision_summary,
)
from app.modules.orchestration.stages import dependency_blockers
from app.modules.projects.service import get_project_for

Kind = Literal["requirements", "architecture"]
router = APIRouter(prefix="/projects/{project_id}", tags=["artifacts"])


class SaveRevisionIn(BaseModel):
    base_revision: int | None = Field(default=None, ge=0)
    content: dict[str, Any]
    summary: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class ValidateIn(BaseModel):
    content: dict[str, Any]


class ValidationOut(BaseModel):
    ok: bool
    issues: list[ValidationIssue]


class DecisionIn(BaseModel):
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


class AnswersIn(BaseModel):
    answers: dict[str, Annotated[str, StringConstraints(max_length=2000)] | None]
    base_revision: int


class ChangeRequestIn(BaseModel):
    instruction: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)
    ]
    base_revision: int


@router.get("/artifacts/{kind}")
async def get_artifact(
    project_id: uuid.UUID, kind: Kind, user: CurrentUser, db: DbSession
) -> ArtifactOut:
    project = await get_project_for(db, user, project_id)
    art = await service.get_artifact(db, project, kind)
    if art is None or art.latest_revision == 0:
        raise NotFound(f"No {kind} yet")
    return artifact_out(art, await service.latest_revision(db, project, kind))


@router.get("/artifacts/{kind}/revisions")
async def list_revisions(
    project_id: uuid.UUID, kind: Kind, user: CurrentUser, db: DbSession
) -> list[RevisionSummaryOut]:
    project = await get_project_for(db, user, project_id)
    return [revision_summary(r) for r in await service.list_revisions(db, project, kind)]


@router.get("/artifacts/{kind}/revisions/{n}")
async def get_revision(
    project_id: uuid.UUID, kind: Kind, n: int, user: CurrentUser, db: DbSession
) -> RevisionOut:
    project = await get_project_for(db, user, project_id)
    return revision_out(await service.get_revision(db, project, kind, n))


async def _sources_for_new_revision(db, project, kind: str) -> list[SourceRef]:
    latest = await service.latest_revision(db, project, kind)
    if latest is not None:
        return latest.sources
    blockers = await dependency_blockers(db, project, kind)
    if blockers:
        raise StageBlocked(blockers)
    if kind == "requirements":
        spec = await db.project_specifications.find_one(
            {"project_id": project.id}, sort=[("revision", -1)]
        )
        return [SourceRef(kind="specification", revision=spec["revision"])]
    req = await service.get_artifact(db, project, "requirements")
    return [SourceRef(kind="requirements", artifact_id=req.id, revision=req.approved_revision)]


@router.post("/artifacts/{kind}/revisions", status_code=status.HTTP_201_CREATED)
async def save_revision(
    project_id: uuid.UUID, kind: Kind, body: SaveRevisionIn, user: CurrentUser, db: DbSession
) -> RevisionOut:
    project = await get_project_for(db, user, project_id)
    sources = await _sources_for_new_revision(db, project, kind)
    rev = await service.add_revision(
        db,
        project,
        kind,
        body.content,
        sources=sources,
        origin="user",
        summary=body.summary or "Edited",
        created_by=user.id,
        base_revision=body.base_revision if body.base_revision is not None else 0,
    )
    return revision_out(rev)


@router.post("/artifacts/{kind}/validate")
async def validate(
    project_id: uuid.UUID, kind: Kind, body: ValidateIn, user: CurrentUser, db: DbSession
) -> ValidationOut:
    project = await get_project_for(db, user, project_id)
    sources = await _sources_for_new_revision(db, project, kind)
    _, result, _ = await service.validate_content(db, project, kind, body.content, sources)
    return ValidationOut(ok=result.ok, issues=result.issues)


@router.post("/artifacts/{kind}/revisions/{n}/restore", status_code=status.HTTP_201_CREATED)
async def restore(
    project_id: uuid.UUID, kind: Kind, n: int, user: CurrentUser, db: DbSession
) -> RevisionOut:
    project = await get_project_for(db, user, project_id)
    return revision_out(await service.restore(db, project, kind, n, user))


@router.post("/artifacts/{kind}/revisions/{n}/approve")
async def approve(
    project_id: uuid.UUID, kind: Kind, n: int, body: DecisionIn, user: CurrentUser, db: DbSession
) -> ApprovalOut:
    project = await get_project_for(db, user, project_id)
    return approval_out(await service.approve(db, project, kind, n, user, body.comment))


@router.post("/artifacts/{kind}/revisions/{n}/request-changes")
async def request_changes(
    project_id: uuid.UUID, kind: Kind, n: int, body: DecisionIn, user: CurrentUser, db: DbSession
) -> ApprovalOut:
    project = await get_project_for(db, user, project_id)
    return approval_out(await service.request_changes(db, project, kind, n, user, body.comment))


@router.put("/artifacts/requirements/answers", status_code=status.HTTP_201_CREATED)
async def answers(
    project_id: uuid.UUID, body: AnswersIn, user: CurrentUser, db: DbSession
) -> RevisionOut:
    project = await get_project_for(db, user, project_id)
    return revision_out(await save_answers(db, project, user, body.answers, body.base_revision))


@router.get("/artifacts/architecture/compare")
async def compare(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    from_: Annotated[int, Query(alias="from", ge=1)],
    to: Annotated[int, Query(ge=1)],
    include_layout: bool = False,
) -> DiagramDiff:
    project = await get_project_for(db, user, project_id)
    a = await service.get_revision(db, project, "architecture", from_)
    b = await service.get_revision(db, project, "architecture", to)
    return diff(
        ArchitectureContent.model_validate(a.content),
        ArchitectureContent.model_validate(b.content),
        include_layout,
    )


@router.post("/artifacts/architecture/change-requests", status_code=status.HTTP_202_ACCEPTED)
async def change_request(
    project_id: uuid.UUID, body: ChangeRequestIn, user: CurrentUser, db: DbSession
) -> RunOut:
    project = await get_project_for(db, user, project_id)
    art = await service.get_artifact(db, project, "architecture")
    if art is not None and art.latest_revision and body.base_revision != art.latest_revision:
        raise RevisionConflict(art.latest_revision)
    run, _ = await orchestration.start_stage(
        db,
        project,
        "architecture",
        "change_request",
        user,
        instruction=body.instruction,
        base_revision=body.base_revision,
    )
    return RunOut.of(run)


@router.get("/approvals")
async def approvals(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[ApprovalOut]:
    project = await get_project_for(db, user, project_id)
    return [approval_out(a) for a in await service.list_approvals(db, project)]
