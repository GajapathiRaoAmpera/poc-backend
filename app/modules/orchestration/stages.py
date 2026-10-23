"""Stage definitions and derived stage status (spec §4.3). Status is computed, never stored."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.db import Db
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import Artifact, Revision
from app.modules.orchestration.models import AgentRun, RunStatus
from app.modules.projects.models import Project

STAGES = ["requirements", "architecture", "plan", "budget", "code"]
AVAILABLE = {"requirements", "architecture"}
AGENTS = {"requirements": "requirements_analyst", "architecture": "solution_architect"}
DEPENDS_ON = {"architecture": ["requirements"]}
LABEL = {
    "requirements": "Requirements",
    "architecture": "Architecture",
    "plan": "Project plan",
    "budget": "Budget",
    "code": "Code generation",
}

# stage → "does this content still need user input?" (registered by agent modules)
INPUT_CHECKS: dict[str, Callable[[dict[str, Any]], bool]] = {}


@dataclass
class StageState:
    stage: str
    status: str
    reasons: list[str] = field(default_factory=list)
    artifact: Artifact | None = None
    latest_revision: Revision | None = None
    latest_run: AgentRun | None = None


async def latest_run(db: Db, project: Project, stage: str) -> AgentRun | None:
    doc = await db.agent_runs.find_one(
        {"project_id": project.id, "stage": stage}, sort=[("created_at", -1)]
    )
    return AgentRun.from_doc(doc) if doc else None


async def dependency_blockers(db: Db, project: Project, stage: str) -> list[str]:
    if stage == "requirements":
        has_spec = await db.project_specifications.count_documents(
            {"project_id": project.id}, limit=1
        )
        return (
            [] if has_spec else ["Add a project specification first (complete the project wizard)"]
        )
    reasons = []
    for dep in DEPENDS_ON.get(stage, []):
        art = await artifacts.get_artifact(db, project, dep)
        if art is None or art.approved_revision is None:
            reasons.append(f"{LABEL[dep]} must be approved first")
        elif art.stale:
            reasons.append(f"{LABEL[dep]} is stale and must be re-approved: {art.stale_reason}")
    return reasons


async def stage_status(db: Db, project: Project, stage: str) -> StageState:
    if stage not in AVAILABLE:
        return StageState(stage, "UNAVAILABLE", ["Available in a coming release"])

    run = await latest_run(db, project, stage)
    art = await artifacts.get_artifact(db, project, stage)
    rev = (
        await artifacts.latest_revision(db, project, stage) if art and art.latest_revision else None
    )
    state = StageState(stage, "NOT_STARTED", [], art, rev, run)

    if run and run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
        state.status = run.status.value
        return state
    if art and art.stale:
        state.status, state.reasons = "STALE", [art.stale_reason or "An upstream artifact changed"]
        return state
    blockers = await dependency_blockers(db, project, stage)
    if blockers and not rev:
        state.status, state.reasons = "BLOCKED", blockers
        return state
    if (
        run
        and run.status in (RunStatus.FAILED, RunStatus.CANCELLED)
        and (rev is None or run.created_at > rev.created_at)
    ):
        state.status = run.status.value
        if run.error:
            state.reasons = [run.error.message]
        return state
    if rev is None:
        return state
    if art.approved_revision == art.latest_revision:
        state.status = "APPROVED"
        return state
    needs_input = INPUT_CHECKS.get(stage)
    if needs_input and needs_input(rev.content):
        state.status, state.reasons = "WAITING_FOR_INPUT", ["Answer the open questions to continue"]
        return state
    errors = [i.message for i in rev.validation.issues if i.severity == "error"]
    decision = await artifacts.latest_decision(db, project, stage)
    if errors:
        state.status, state.reasons = "NEEDS_REVISION", errors[:3]
        return state
    if decision and decision.revision == rev.revision and decision.decision == "CHANGES_REQUESTED":
        state.status = "NEEDS_REVISION"
        state.reasons = [f"Changes requested: {decision.comment or 'see comments'}"]
        return state
    state.status = "WAITING_FOR_APPROVAL"
    return state


async def workflow(db: Db, project: Project) -> list[StageState]:
    return [await stage_status(db, project, s) for s in STAGES]
