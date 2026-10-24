import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFound
from app.core.pagination import Page, PageParams
from app.modules.artifacts import service as artifacts
from app.modules.orchestration import service, sse
from app.modules.orchestration.events import iter_events
from app.modules.orchestration.models import AgentRun
from app.modules.orchestration.runs import get_run
from app.modules.orchestration.schemas import (
    RunDetailOut,
    RunEventOut,
    RunOut,
    StageName,
    StageStateOut,
    StartRunIn,
    artifact_out,
)
from app.modules.orchestration.stages import AVAILABLE, workflow
from app.modules.projects.service import get_project_for

router = APIRouter(prefix="/projects/{project_id}", tags=["workflow"])


@router.get("/workflow")
async def get_workflow(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[StageStateOut]:
    project = await get_project_for(db, user, project_id)
    out = []
    for s in await workflow(db, project):
        out.append(
            StageStateOut(
                stage=s.stage,
                status=s.status,
                reasons=s.reasons,
                available=s.stage in AVAILABLE,
                artifact=artifact_out(s.artifact)
                if s.artifact and s.artifact.latest_revision
                else None,
                latest_run=RunOut.of(s.latest_run) if s.latest_run else None,
            )
        )
    return out


@router.post("/stages/{stage}/runs", response_model=RunOut)
async def start_run(
    project_id: uuid.UUID, stage: StageName, body: StartRunIn, user: CurrentUser, db: DbSession
) -> JSONResponse:
    project = await get_project_for(db, user, project_id)
    run, created = await service.start_stage(db, project, stage, body.mode, user)
    return JSONResponse(
        RunOut.of(run).model_dump(mode="json"),
        status_code=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
    )


@router.get("/runs")
async def list_runs(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    params: PageParams,
    stage: StageName | None = None,
) -> Page[RunOut]:
    project = await get_project_for(db, user, project_id)
    query: dict = {"project_id": project.id}
    if stage:
        query["stage"] = stage
    total = await db.agent_runs.count_documents(query)
    cursor = (
        db.agent_runs.find(query).sort("created_at", -1).skip(params.offset).limit(params.page_size)
    )
    items = [RunOut.of(AgentRun.from_doc(d)) async for d in cursor]
    return Page(items=items, page=params.page, page_size=params.page_size, total=total)


async def _run_or_404(db, project, run_id) -> AgentRun:
    run = await get_run(db, project.id, run_id)
    if run is None:
        raise NotFound("Run not found")
    return run


@router.get("/runs/{run_id}")
async def run_detail(
    project_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> RunDetailOut:
    project = await get_project_for(db, user, project_id)
    run = await _run_or_404(db, project, run_id)
    events = [RunEventOut.of(e) async for e in iter_events(db, project.id, run_id=run.id)]
    return RunDetailOut(**RunOut.of(run).model_dump(), events=events)


@router.post("/runs/{run_id}/cancel")
async def cancel(
    project_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> RunOut:
    project = await get_project_for(db, user, project_id)
    return RunOut.of(await service.cancel_run(db, project, run_id, user))


@router.post("/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry(
    project_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> RunOut:
    project = await get_project_for(db, user, project_id)
    return RunOut.of(await service.retry_run(db, project, run_id, user))


@router.get("/events")
async def events_stream(
    project_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    after: Annotated[int, Query(ge=0)] = 0,
    max_wait: Annotated[float, Query(gt=0, le=300)] = 300,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    project = await get_project_for(db, user, project_id)
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    return StreamingResponse(
        sse.event_stream(request, db, project.id, after, max_wait),
        media_type="text/event-stream",
        # no-transform: stops compressing proxies (e.g. Next.js rewrites) buffering the stream.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


__all__ = ["artifacts", "router"]
