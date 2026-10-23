"""Server-Sent Events for project activity. Clients resume with Last-Event-ID / ?after=."""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import Request

from app.core.db import Db
from app.modules.orchestration.events import iter_events
from app.modules.orchestration.schemas import RunEventOut

POLL_S = 1.0
KEEPALIVE_S = 15.0


async def event_stream(
    request: Request, db: Db, project_id: uuid.UUID, after: int, max_wait: float
) -> AsyncIterator[str]:
    cursor = after
    started = last_sent = time.monotonic()
    yield "retry: 3000\n\n"
    while time.monotonic() - started < max_wait:
        if await request.is_disconnected():
            return
        sent = False
        async for e in iter_events(db, project_id, after=cursor):
            cursor = e.seq
            payload = RunEventOut.of(e).model_dump(mode="json")
            yield f"id: {e.seq}\nevent: run_event\ndata: {json.dumps(payload)}\n\n"
            sent = True
        now = time.monotonic()
        if sent:
            last_sent = now
        elif now - last_sent >= KEEPALIVE_S:
            yield ": keepalive\n\n"
            last_sent = now
        await asyncio.sleep(min(POLL_S, max(0.0, max_wait - (now - started))))
