"""LangGraph checkpoint storage in the application's MongoDB database.

Decision: the official ``langgraph-checkpoint-mongodb`` ``MongoDBSaver``. It uses a synchronous
``MongoClient`` and runs its async methods in a thread executor; that is acceptable here because
only the worker drives graphs and a checkpoint write is a single small document. It gets its own
sync client (same URI and database as the app) — the app's ``AsyncMongoClient`` cannot be shared.
"""

from functools import lru_cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from app.core.config import get_settings
from app.core.db import Db

CHECKPOINTS = "graph_checkpoints"
CHECKPOINT_WRITES = "graph_checkpoint_writes"


@lru_cache
def _sync_client(uri: str) -> MongoClient:
    return MongoClient(
        uri, uuidRepresentation="standard", tz_aware=True, appname="ampera-delivery-graph"
    )


def make_checkpointer(db: Db) -> BaseCheckpointSaver:
    return MongoDBSaver(
        _sync_client(get_settings().mongodb_uri),
        db_name=db.name,
        checkpoint_collection_name=CHECKPOINTS,
        writes_collection_name=CHECKPOINT_WRITES,
    )
