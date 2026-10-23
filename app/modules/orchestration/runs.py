"""Agent-run state machine. Every status change goes through ``transition``."""

import uuid
from typing import Any

from pymongo import ReturnDocument

from app.core.db import Db
from app.core.documents import now
from app.modules.orchestration.models import ACTIVE, AgentRun, RunStatus

__all__ = ["IllegalTransition", "check_transition", "get_run", "now", "transition"]

LEGAL: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.QUEUED,
    },  # QUEUED = retry after a transient failure
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


class IllegalTransition(Exception):
    pass


def check_transition(a: RunStatus, b: RunStatus) -> None:
    if b not in LEGAL[a]:
        raise IllegalTransition(f"{a} → {b} is not allowed")


async def transition(
    db: Db, run_id: uuid.UUID, from_: set[RunStatus], to: RunStatus, session=None, **fields: Any
) -> AgentRun:
    for a in from_:
        check_transition(a, to)
    update: dict[str, Any] = {"status": to.value, "active": to in ACTIVE, **fields}
    if to in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
        update.setdefault("finished_at", now())
    doc = await db.agent_runs.find_one_and_update(
        {"_id": run_id, "status": {"$in": [s.value for s in from_]}},
        {"$set": update},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if doc is None:
        raise IllegalTransition(f"Run {run_id} is not in {sorted(from_)}")
    return AgentRun.from_doc(doc)


async def get_run(db: Db, project_id: uuid.UUID, run_id: uuid.UUID) -> AgentRun | None:
    doc = await db.agent_runs.find_one({"_id": run_id, "project_id": project_id})
    return AgentRun.from_doc(doc) if doc else None
