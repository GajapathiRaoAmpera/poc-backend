import uuid

import pytest
from pymongo import ASCENDING, IndexModel
from pymongo.errors import DuplicateKeyError

from app.core.db import ensure_indexes
from app.modules.orchestration.models import Job


async def test_legacy_run_id_index_migrated(db):
    await db.jobs.drop_indexes()
    await db.jobs.create_indexes([IndexModel([("run_id", ASCENDING)], unique=True)])
    await ensure_indexes(db)
    names = {i["name"] async for i in await db.jobs.list_indexes()}
    assert "run_id_1" not in names
    assert "one_job_per_run" in names


async def test_two_graph_jobs_same_key_conflict(db):
    pid = uuid.uuid4()
    key = f"graph:{pid}"
    await db.jobs.insert_one(Job(project_id=pid, type="graph_step", idempotency_key=key).to_doc())
    with pytest.raises(DuplicateKeyError):
        await db.jobs.insert_one(
            Job(project_id=pid, type="graph_step", idempotency_key=key).to_doc()
        )


async def test_graph_jobs_without_run_id_coexist(db):
    for _ in range(2):
        await db.jobs.insert_one(Job(project_id=uuid.uuid4(), type="graph_step").to_doc())
    assert await db.jobs.count_documents({"type": "graph_step"}) == 2


async def test_agent_run_jobs_still_unique_per_run(db):
    rid = uuid.uuid4()
    await db.jobs.insert_one(Job(run_id=rid, project_id=uuid.uuid4()).to_doc())
    with pytest.raises(DuplicateKeyError):
        await db.jobs.insert_one(Job(run_id=rid, project_id=uuid.uuid4()).to_doc())
