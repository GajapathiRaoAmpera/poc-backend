"""Starting, resuming, nudging and cancelling a project's delivery graph."""

from typing import Any

from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.db import Db
from app.core.errors import Conflict, GraphBusy, NotPaused
from app.modules.auth.models import User
from app.modules.delivery.threads import DeliveryThread, get_thread, set_thread
from app.modules.orchestration.models import Job
from app.modules.projects.models import Project

# Thread pauses the user must explicitly resume; ordinary actions don't restart them.
STICKY = {"cancelled"}
REPLAN_TRIGGER = "user_message"


def graph_key(project: Project) -> str:
    return f"graph:{project.id}"


async def _enqueue(db: Db, project: Project, mode: str, trigger: str, message: str | None) -> bool:
    job = Job(
        project_id=project.id,
        type="graph_step",
        idempotency_key=graph_key(project),
        input={"mode": mode, "trigger": trigger, "message": message},
        timeout_s=3 * get_settings().agent_timeout_s,
    )
    try:
        await db.jobs.insert_one(job.to_doc())
    except DuplicateKeyError:
        return False  # a graph job for this project is already queued or running
    await set_thread(db, project.id, status="RUNNING", pending=None, cancel_requested=False)
    return True


async def start_delivery(db: Db, project: Project, user: User) -> DeliveryThread:
    thread = DeliveryThread(project_id=project.id, owner_id=project.owner_id, started_by=user.id)
    try:
        await db.delivery_threads.insert_one(thread.to_doc())
    except DuplicateKeyError:
        pass
    await _enqueue(db, project, "start", "intake", None)
    return await get_thread(db, project)


async def notify(db: Db, project: Project, trigger: str, message: str | None = None) -> bool:
    """Something changed (approval, answers, a message…): let the graph react. True = enqueued."""
    thread = await get_thread(db, project)
    if thread is None:
        return False
    if thread.status == "RUNNING":
        # Remember it for when the graph pauses; never downgrade a re-plan trigger.
        await db.delivery_threads.update_one(
            {"project_id": project.id, "dirty": {"$ne": REPLAN_TRIGGER}},
            {"$set": {"dirty": trigger}},
        )
        return False
    if thread.status == "PAUSED" and (thread.pending or {}).get("kind") not in STICKY:
        return await _enqueue(db, project, "resume", trigger, message)
    if thread.status == "DONE":
        return await _enqueue(db, project, "start", trigger, message)
    return False


async def resume(db: Db, project: Project, user: User) -> DeliveryThread:
    thread = await get_thread(db, project)
    if thread is None:
        raise NotPaused("This project has no delivery workflow yet.")
    if thread.status == "RUNNING":
        raise GraphBusy()
    if thread.status not in ("PAUSED", "FAILED"):
        raise NotPaused()
    mode = "continue" if thread.status == "FAILED" else "resume"
    if not await _enqueue(db, project, mode, "retry", None):
        raise GraphBusy()
    return await get_thread(db, project)


async def cancel(db: Db, project: Project, user: User) -> DeliveryThread:
    thread = await get_thread(db, project)
    if thread is None or thread.status != "RUNNING":
        raise Conflict("Nothing is running")
    queued = await db.jobs.find_one_and_update(
        {"idempotency_key": graph_key(project), "status": "QUEUED"},
        {"$set": {"status": "DONE", "idempotency_key": None}},
    )
    if queued:
        await set_thread(db, project.id, status="PAUSED", pending={"kind": "cancelled"})
    else:
        await set_thread(db, project.id, cancel_requested=True)
    return await get_thread(db, project)


def job_input(job: Job) -> dict[str, Any]:
    return job.input or {"mode": "start", "trigger": None, "message": None}
