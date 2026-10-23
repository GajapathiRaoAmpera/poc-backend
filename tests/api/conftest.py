import copy

import pytest

from app.llm.mock import MockProvider
from app.worker.runner import Worker
from tests.agents.helpers import SPEC
from tests.factories import login, make_user


@pytest.fixture
async def owner_client(client, db):
    await make_user(db, email="owner@x.com")
    await login(client, "owner@x.com")
    return client


@pytest.fixture
async def project_id(owner_client):
    p = (await owner_client.post("/api/v1/projects", json={"name": "Portal"})).json()
    r = await owner_client.put(
        f"/api/v1/projects/{p['id']}/specification", json=copy.deepcopy(SPEC)
    )
    assert r.status_code == 201
    return p["id"]


@pytest.fixture
def work(db):
    async def _work():
        return await Worker(
            db, provider_factory=MockProvider, heartbeat_s=0.05, poll_s=0.01
        ).run_once()

    return _work
