"""One delivery thread per project: the API/UI view of the graph (never raw checkpoints)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.core.db import Db
from app.core.documents import Document, now
from app.modules.orchestration import events
from app.modules.projects.models import Project

ThreadStatus = Literal["IDLE", "RUNNING", "PAUSED", "DONE", "FAILED"]

STAGE_LABEL = {
    "orchestrator": "Project Orchestrator",
    "requirements": "requirements",
    "architecture": "architecture",
    "plan": "plan",
    "budget": "budget",
    "code": "scaffold",
}


class DeliveryThread(Document):
    """Collection ``delivery_threads``."""

    project_id: uuid.UUID
    owner_id: uuid.UUID
    status: ThreadStatus = "IDLE"
    pending: dict[str, Any] | None = None
    plan_version: int | None = None
    last_error: str | None = None
    cancel_requested: bool = False
    # The trigger of an action taken while the graph was running; replayed after it pauses.
    dirty: str | None = None
    started_by: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


async def get_thread(db: Db, project: Project) -> DeliveryThread | None:
    doc = await db.delivery_threads.find_one({"project_id": project.id})
    return DeliveryThread.from_doc(doc) if doc else None


def describe(status: str, pending: dict[str, Any] | None) -> str:
    if status == "RUNNING":
        return "Delivery workflow running"
    if status == "DONE":
        return "Delivery workflow complete: every planned stage is approved"
    if status == "FAILED":
        return f"Delivery workflow failed: {(pending or {}).get('reason') or 'see activity'}"
    if status != "PAUSED" or not pending:
        return f"Delivery workflow {status.lower()}"
    kind, stage = pending.get("kind"), STAGE_LABEL.get(pending.get("stage") or "", "")
    p = pending.get("payload") or {}
    return {
        "approval": f"Waiting for your approval of {stage} revision {p.get('revision')}",
        "awaiting_answers": f"Waiting for answers to {stage} questions",
        "intake_questions": "The Project Orchestrator has questions for you",
        "needs_input": f"Paused: {p.get('reason') or 'needs your input'}",
        "stage_busy": f"Waiting for the {stage} agent's chat run to finish",
        "quota": f"Paused: {p.get('reason') or 'daily agent run limit reached'}",
        "failed": f"Paused: {p.get('reason') or 'an agent failed'}",
        "cancelled": "Delivery workflow cancelled",
    }.get(kind, "Delivery workflow paused")


async def set_thread(db: Db, project_id: uuid.UUID, **fields: Any) -> None:
    """Update the thread and announce the change as a ``graph`` event when status/pending moved."""
    doc = await db.delivery_threads.find_one_and_update(
        {"project_id": project_id}, {"$set": {**fields, "updated_at": now()}}
    )
    if doc is None or ("status" not in fields and "pending" not in fields):
        return
    status = fields.get("status", doc["status"])
    pending = fields.get("pending", doc.get("pending"))
    if status == doc["status"] and pending == doc.get("pending"):
        return
    text = describe(status, pending)
    await events.emit(db, project_id, None, "graph", text, {"status": status, "pending": pending})
    await _announce(db, project_id, status, pending, text)


async def _announce(db: Db, project_id: uuid.UUID, status: str, pending, text: str) -> None:
    """Post pauses and completion into the chat where the person will act on them."""
    from app.modules.conversations.models import STAGE_CHANNELS
    from app.modules.conversations.service import add_message

    kind = (pending or {}).get("kind")
    if status == "RUNNING" or kind == "intake_questions":  # the orchestrator already asked
        return
    stage = (pending or {}).get("stage")
    channel = stage if stage in STAGE_CHANNELS else "intake"
    last = await db.messages.find_one(
        {"project_id": project_id, "channel": channel},
        sort=[("seq", -1)],
        projection={"role": 1, "text": 1},
    )
    if last and last["role"] == "system" and last["text"] == text:
        return  # a re-check that ends where it started says nothing new
    project = Project.from_doc(await db.projects.find_one({"_id": project_id}))
    await add_message(db, project, channel, "system", text, refs={"gate": kind} if kind else None)
