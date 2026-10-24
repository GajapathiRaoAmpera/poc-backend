"""The orchestrator: decides what may run, records it, and enqueues it. Agents never chain."""

import uuid
from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.db import Db, in_transaction
from app.core.documents import now
from app.core.errors import Conflict, NotFound, QuotaExceeded, StageBlocked
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.auth.models import User
from app.modules.orchestration import events, runs
from app.modules.orchestration.models import AgentRun, Job, RunMode, RunStatus
from app.modules.orchestration.stages import AGENTS, AVAILABLE, LABEL, dependency_blockers
from app.modules.projects.models import Project


async def _quota_check(db: Db, user: User) -> None:
    limit = (
        user.daily_run_limit
        if user.daily_run_limit is not None
        else (get_settings().platform_daily_run_limit)
    )
    used = await db.agent_runs.count_documents(
        {"created_by": user.id, "created_at": {"$gt": now() - timedelta(hours=24)}}
    )
    if used >= limit:
        raise QuotaExceeded(limit)


async def _resolve_inputs(
    db: Db, project: Project, stage: str, mode: RunMode, base_revision: int | None
) -> list[SourceRef]:
    if stage == "requirements":
        last = await db.project_specifications.find_one(
            {"project_id": project.id}, sort=[("revision", -1)]
        )
        inputs = [SourceRef(kind="specification", revision=last["revision"])]
        if mode == "chat":
            art = await artifacts.get_artifact(db, project, "requirements")
            if art is not None and art.latest_revision:
                inputs.append(
                    SourceRef(kind="requirements", artifact_id=art.id, revision=art.latest_revision)
                )
        if mode == "revise":
            art = await artifacts.get_artifact(db, project, "requirements")
            if art is None or art.latest_revision == 0:
                raise StageBlocked(["There are no requirements to revise yet"])
            inputs.append(
                SourceRef(kind="requirements", artifact_id=art.id, revision=art.latest_revision)
            )
        return inputs
    req = await artifacts.get_artifact(db, project, "requirements")
    inputs = [SourceRef(kind="requirements", artifact_id=req.id, revision=req.approved_revision)]
    if mode == "chat":
        arch = await artifacts.get_artifact(db, project, "architecture")
        if arch is not None and arch.latest_revision:
            inputs.append(
                SourceRef(kind="architecture", artifact_id=arch.id, revision=arch.latest_revision)
            )
    if mode == "change_request":
        arch = await artifacts.get_artifact(db, project, "architecture")
        if arch is None or arch.latest_revision == 0:
            raise StageBlocked(["Generate an architecture before requesting changes"])
        inputs.append(
            SourceRef(
                kind="architecture",
                artifact_id=arch.id,
                revision=base_revision or arch.latest_revision,
            )
        )
    return inputs


async def _active_run(db: Db, project: Project, stage: str) -> AgentRun | None:
    doc = await db.agent_runs.find_one({"project_id": project.id, "stage": stage, "active": True})
    return AgentRun.from_doc(doc) if doc else None


async def _create_run(db: Db, project: Project, run: AgentRun) -> tuple[AgentRun, bool]:
    job = Job(run_id=run.id, project_id=project.id, timeout_s=get_settings().agent_timeout_s)

    async def txn(session) -> None:
        await db.agent_runs.insert_one(run.to_doc(), session=session)
        await db.jobs.insert_one(job.to_doc(), session=session)
        await events.emit(
            db,
            project.id,
            run.id,
            "status",
            f"Queued {run.agent.replace('_', ' ').title()} ({run.mode})",
            session=session,
        )

    try:
        await in_transaction(db, txn)
    except DuplicateKeyError:
        # Another request created the active run for this stage first: return that one.
        existing = await _active_run(db, project, run.stage)
        if existing is None:
            raise
        return existing, False
    return run, True


async def start_stage(
    db: Db,
    project: Project,
    stage: str,
    mode: RunMode,
    user: User,
    instruction: str | None = None,
    base_revision: int | None = None,
) -> tuple[AgentRun, bool]:
    if stage not in AVAILABLE:
        raise StageBlocked([f"{LABEL.get(stage, stage)} is available in a coming release"])
    if mode == "change_request" and stage != "architecture":
        raise StageBlocked(["Change requests are only supported for the architecture"])
    blockers = await dependency_blockers(db, project, stage)
    if blockers:
        raise StageBlocked(blockers)
    existing = await _active_run(db, project, stage)
    if existing:
        return existing, False
    await _quota_check(db, user)
    inputs = await _resolve_inputs(db, project, stage, mode, base_revision)
    run = AgentRun(
        project_id=project.id,
        owner_id=project.owner_id,
        stage=stage,
        agent=AGENTS[stage],
        mode=mode,
        inputs=inputs,
        instruction=instruction,
        base_revision=base_revision,
        created_by=user.id,
    )
    return await _create_run(db, project, run)


async def _get(db: Db, project: Project, run_id: uuid.UUID) -> AgentRun:
    run = await runs.get_run(db, project.id, run_id)
    if run is None:
        raise NotFound("Run not found")
    return run


async def cancel_run(db: Db, project: Project, run_id: uuid.UUID, user: User) -> AgentRun:
    run = await _get(db, project, run_id)
    if run.status == RunStatus.QUEUED:
        try:
            out = await runs.transition(db, run.id, {RunStatus.QUEUED}, RunStatus.CANCELLED)
            await db.jobs.update_one({"run_id": run.id}, {"$set": {"status": "DONE"}})
            await events.emit(db, project.id, run.id, "status", "Cancelled before it started")
            return out
        except runs.IllegalTransition:
            run = await _get(db, project, run_id)  # a worker picked it up meanwhile
    if run.status == RunStatus.RUNNING:
        await db.agent_runs.update_one({"_id": run.id}, {"$set": {"cancel_requested": True}})
        await events.emit(db, project.id, run.id, "status", "Cancellation requested")
        return await _get(db, project, run_id)
    raise Conflict("This run has already finished")


async def retry_run(db: Db, project: Project, run_id: uuid.UUID, user: User) -> AgentRun:
    old = await _get(db, project, run_id)
    if old.status not in (RunStatus.FAILED, RunStatus.CANCELLED):
        raise Conflict("Only failed or cancelled runs can be retried")
    if old.graph_key:
        # Runs the delivery graph started are retried by resuming the graph, which re-runs the
        # step; some (the orchestrator) have no standalone agent to run outside it.
        from app.modules.delivery import service as delivery

        await delivery.resume(db, project, user)
        return old
    blockers = await dependency_blockers(db, project, old.stage)
    if blockers:
        raise StageBlocked(blockers)
    existing = await _active_run(db, project, old.stage)
    if existing:
        return existing
    await _quota_check(db, user)
    run = AgentRun(
        project_id=project.id,
        owner_id=project.owner_id,
        stage=old.stage,
        agent=old.agent,
        mode=old.mode,
        inputs=old.inputs,
        instruction=old.instruction,
        base_revision=old.base_revision,
        created_by=user.id,
        retry_of=old.id,
    )
    new, _ = await _create_run(db, project, run)
    return new
