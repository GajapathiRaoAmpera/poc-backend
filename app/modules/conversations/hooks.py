from app.core.db import Db
from app.modules.conversations.service import add_message
from app.modules.orchestration.models import AgentRun, RunStatus
from app.modules.projects.models import Project


async def on_run_finished(db: Db, run: AgentRun) -> None:
    """A stage chat run ended: report failures in the chat and let the delivery graph react."""
    if run.mode != "chat" or run.stage not in ("requirements", "architecture"):
        return
    from app.modules.delivery.service import notify

    project = Project.from_doc(await db.projects.find_one({"_id": run.project_id}))
    if run.status == RunStatus.FAILED:
        reason = run.error.message if run.error else "unknown error"
        await add_message(
            db,
            project,
            run.stage,
            "system",
            f"The agent couldn't finish: {reason}",
            refs={"run_id": str(run.id)},
        )
    elif run.status == RunStatus.CANCELLED:
        await add_message(
            db,
            project,
            run.stage,
            "system",
            "The agent's reply was cancelled",
            refs={"run_id": str(run.id)},
        )
    await notify(db, project, "chat_finished")
