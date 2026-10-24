import uuid
from collections.abc import AsyncIterator
from typing import Any

from pymongo import ReturnDocument

from app.core.db import Db
from app.modules.orchestration.models import EventType, RunEvent


async def _next_seq(db: Db, project_id: uuid.UUID, session=None) -> int:
    doc = await db.counters.find_one_and_update(
        {"_id": f"events:{project_id}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    return doc["seq"]


async def emit(
    db: Db,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None,
    type: EventType,
    message: str,
    data: dict[str, Any] | None = None,
    session=None,
) -> int:
    """Append an activity event. Messages are user-facing: no secrets, no hidden reasoning."""
    seq = await _next_seq(db, project_id, session)
    event = RunEvent(
        project_id=project_id, run_id=run_id, seq=seq, type=type, message=message[:500], data=data
    )
    await db.run_events.insert_one(event.to_doc(), session=session)
    return seq


async def iter_events(
    db: Db, project_id: uuid.UUID, after: int = 0, run_id: uuid.UUID | None = None, limit: int = 500
) -> AsyncIterator[RunEvent]:
    query: dict[str, Any] = {"project_id": project_id, "seq": {"$gt": after}}
    if run_id:
        query["run_id"] = run_id
    async for doc in db.run_events.find(query).sort("seq", 1).limit(limit):
        yield RunEvent.from_doc(doc)
