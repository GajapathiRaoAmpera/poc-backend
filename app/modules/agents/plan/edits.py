"""Timeline edits by a person: pin a start ("not before") or change effort, then recompute the
schedule in code (no LLM). Saved as a user revision on the latest plan."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.deps import CurrentUser, DbSession
from app.core.errors import AppError, NotFound, RevisionConflict
from app.modules.agents.plan.schedule import compute_schedule
from app.modules.agents.plan.schema import PlanAgentOutput, PlanContent, Task
from app.modules.artifacts import service as artifacts
from app.modules.delivery.service import notify
from app.modules.orchestration.schemas import RevisionOut, revision_out
from app.modules.projects.service import get_project_for

router = APIRouter(prefix="/projects/{project_id}/plan", tags=["artifacts"])


class TaskEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: Annotated[str, StringConstraints(max_length=20)]
    # Present and null clears the pin; absent leaves it unchanged.
    not_before: date | None = None
    effort_days: float | None = None

    @field_validator("effort_days")
    @classmethod
    def _effort(cls, v: float | None) -> float | None:
        if v is not None:
            Task.model_validate(
                {"id": "T-000", "title": "x", "milestone_id": "M-01", "role": "x", "effort_days": v}
            )
        return v


class PlanEditsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int
    edits: Annotated[list[TaskEdit], Field(min_length=1, max_length=150)]


def apply_edits(content: PlanContent, edits: list[TaskEdit], prefs: dict[str, Any]) -> PlanContent:
    tasks = {t.id: t.model_dump(exclude={"start", "end"}) for t in content.tasks}
    for e in edits:
        if e.task_id not in tasks:
            raise AppError("UNKNOWN_TASK", f"Task {e.task_id} is not in this plan", 422)
        if "not_before" in e.model_fields_set:
            tasks[e.task_id]["not_before"] = e.not_before
        if e.effort_days is not None:
            tasks[e.task_id]["effort_days"] = e.effort_days
    out = PlanAgentOutput(
        summary=content.summary,
        milestones=[m.model_dump(exclude={"start", "end"}) for m in content.milestones],
        tasks=list(tasks.values()),
        risks=content.risks,
    )
    # Same anchors as the agent: the preferred start, else where this plan already starts.
    start = date.fromisoformat(prefs["start_date"]) if prefs.get("start_date") else None
    target = prefs.get("target_delivery_date")
    return compute_schedule(
        out,
        start or content.schedule.start,
        prefs.get("team_size") or {},
        date.fromisoformat(target) if target else None,
    )


@router.post("/edits", status_code=status.HTTP_201_CREATED)
async def save_edits(
    project_id: uuid.UUID, body: PlanEditsIn, user: CurrentUser, db: DbSession
) -> RevisionOut:
    project = await get_project_for(db, user, project_id)
    art = await artifacts.get_artifact(db, project, "plan")
    if art is None or art.latest_revision == 0:
        raise NotFound("No plan yet")
    if body.base_revision != art.latest_revision:
        raise RevisionConflict(art.latest_revision)
    latest = await artifacts.latest_revision(db, project, "plan")
    content = apply_edits(
        PlanContent.model_validate(latest.content), body.edits, project.preferences or {}
    )
    n = len({e.task_id for e in body.edits})
    rev = await artifacts.add_revision(
        db,
        project,
        "plan",
        content.model_dump(mode="json"),
        sources=latest.sources,
        origin="user",
        summary=f"Adjusted {n} task{'s' if n != 1 else ''} on the timeline",
        created_by=user.id,
        base_revision=body.base_revision,
    )
    await notify(db, project, "edited:plan")
    return revision_out(rev)
