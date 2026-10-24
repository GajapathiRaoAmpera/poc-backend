"""Per-project conversations: intake (with the orchestrator) and one per stage agent."""

from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.db import Db
from app.modules.conversations.models import Channel, Conversation, Message, Role
from app.modules.orchestration import events
from app.modules.projects.models import Project

MAX_TEXT = 4000


async def get_conversation(db: Db, project: Project, channel: Channel) -> Conversation:
    doc = await db.conversations.find_one({"project_id": project.id, "channel": channel})
    if doc:
        return Conversation.from_doc(doc)
    conv = Conversation(project_id=project.id, owner_id=project.owner_id, channel=channel)
    try:
        await db.conversations.insert_one(conv.to_doc())
        return conv
    except DuplicateKeyError:
        doc = await db.conversations.find_one({"project_id": project.id, "channel": channel})
        return Conversation.from_doc(doc)


async def add_message(
    db: Db,
    project: Project,
    channel: Channel,
    role: Role,
    text: str,
    agent: str | None = None,
    refs: dict[str, Any] | None = None,
) -> Message:
    conv = await get_conversation(db, project, channel)
    counter = await db.counters.find_one_and_update(
        {"_id": f"msg:{conv.id}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    msg = Message(
        conversation_id=conv.id,
        project_id=project.id,
        owner_id=project.owner_id,
        channel=channel,
        seq=counter["seq"],
        role=role,
        agent=agent,
        text=text.strip()[:MAX_TEXT],
        refs={k: v for k, v in (refs or {}).items() if v is not None},
    )
    await db.messages.insert_one(msg.to_doc())
    await events.emit(
        db,
        project.id,
        None,
        "message",
        f"New {role} message in {channel}",
        {"channel": channel, "message_id": str(msg.id), "seq": msg.seq},
    )
    return msg


async def list_messages(
    db: Db, project: Project, channel: Channel, after: int = 0, limit: int = 100
) -> list[Message]:
    cursor = (
        db.messages.find({"project_id": project.id, "channel": channel, "seq": {"$gt": after}})
        .sort("seq", 1)
        .limit(limit)
    )
    return [Message.from_doc(d) async for d in cursor]


async def recent_messages(db: Db, project: Project, channel: Channel, n: int = 20) -> list[Message]:
    cursor = db.messages.find({"project_id": project.id, "channel": channel}).sort("seq", -1)
    return list(reversed([Message.from_doc(d) async for d in cursor.limit(n)]))
