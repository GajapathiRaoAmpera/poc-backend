"""MongoDB access: one client per process, a database handle, and index setup.

Indexes replace the old Postgres constraints (uniqueness) and add TTL clean-up.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import get_settings

Db = AsyncDatabase[dict[str, Any]]

_client: AsyncMongoClient | None = None


def make_client(uri: str | None = None) -> AsyncMongoClient:
    return AsyncMongoClient(
        uri or get_settings().mongodb_uri,
        uuidRepresentation="standard",
        tz_aware=True,
        serverSelectionTimeoutMS=10_000,
        appname="ampera-delivery-api",
    )


def get_client() -> AsyncMongoClient:
    global _client
    if _client is None:
        _client = make_client()
    return _client


def database(client: AsyncMongoClient | None = None) -> Db:
    return (client or get_client())[get_settings().mongodb_db]


async def get_db() -> AsyncIterator[Db]:
    yield database()


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def in_transaction[T](db: Db, fn: Callable[[AsyncClientSession], Awaitable[T]]) -> T:
    """Run ``fn`` in a transaction, retrying transient conflicts (requires a replica set)."""
    async with db.client.start_session() as session:
        return await session.with_transaction(fn)


INDEXES: dict[str, list[IndexModel]] = {
    "users": [IndexModel([("email", ASCENDING)], unique=True)],
    "sessions": [
        IndexModel([("token_hash", ASCENDING)], unique=True),
        IndexModel([("user_id", ASCENDING)]),
        # Expired sessions are deleted by MongoDB itself.
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ],
    "password_reset_tokens": [
        IndexModel([("token_hash", ASCENDING)], unique=True),
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ],
    "login_attempts": [
        IndexModel([("email", ASCENDING), ("ip", ASCENDING), ("created_at", ASCENDING)]),
        IndexModel([("created_at", ASCENDING)], expireAfterSeconds=24 * 3600),
    ],
    "projects": [IndexModel([("owner_id", ASCENDING), ("updated_at", DESCENDING)])],
    "project_specifications": [
        IndexModel([("project_id", ASCENDING), ("revision", ASCENDING)], unique=True)
    ],
    "audit_logs": [IndexModel([("created_at", DESCENDING)])],
    "artifacts": [IndexModel([("project_id", ASCENDING), ("kind", ASCENDING)], unique=True)],
    "artifact_revisions": [
        IndexModel([("artifact_id", ASCENDING), ("revision", ASCENDING)], unique=True),
        IndexModel([("project_id", ASCENDING), ("kind", ASCENDING), ("revision", DESCENDING)]),
        # Idempotent agent writes: one revision per run (absent for user edits).
        IndexModel([("run_id", ASCENDING)], unique=True, sparse=True),
    ],
    "agent_runs": [
        IndexModel([("project_id", ASCENDING), ("stage", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("created_by", ASCENDING), ("created_at", DESCENDING)]),
        # At most one active (QUEUED/RUNNING) run per project stage.
        IndexModel(
            [("project_id", ASCENDING), ("stage", ASCENDING)],
            unique=True,
            partialFilterExpression={"active": True},
            name="one_active_run_per_stage",
        ),
    ],
    "jobs": [
        IndexModel([("status", ASCENDING), ("run_after", ASCENDING)]),
        IndexModel([("run_id", ASCENDING)], unique=True),
    ],
    "run_events": [IndexModel([("project_id", ASCENDING), ("seq", ASCENDING)], unique=True)],
    "change_sets": [
        IndexModel([("project_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("run_id", ASCENDING)], unique=True, sparse=True),
    ],
    "approvals": [
        IndexModel([("project_id", ASCENDING), ("kind", ASCENDING), ("decided_at", DESCENDING)])
    ],
}


async def ensure_indexes(db: Db) -> None:
    """Idempotent: safe to call on every startup."""
    for name, indexes in INDEXES.items():
        await db[name].create_indexes(indexes)
