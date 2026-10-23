import os

from tests.safety import assert_safe_test_database

# Never inherit MONGODB_URI from the shell or .env: the suite deletes every document.
TEST_MONGODB_URI = os.environ.get(
    "TEST_MONGODB_URI", "mongodb://localhost:27018/?directConnection=true"
)
TEST_MONGODB_DB = os.environ.get("TEST_MONGODB_DB", "ampera_test")
assert_safe_test_database(TEST_MONGODB_URI, TEST_MONGODB_DB)
os.environ["MONGODB_URI"] = TEST_MONGODB_URI
os.environ["MONGODB_DB"] = TEST_MONGODB_DB
os.environ["ENVIRONMENT"] = "test"
os.environ["APP_ORIGIN"] = "http://localhost:3000"
os.environ.pop("TRUSTED_PROXY_IPS", None)
# Tests never call a real model provider.
os.environ["LLM_PROVIDER"] = "mock"
os.environ.pop("GROQ_API_KEY", None)

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.core.db import INDEXES, database, ensure_indexes  # noqa: E402
from app.main import create_app  # noqa: E402

APP_ORIGIN = os.environ["APP_ORIGIN"]


@pytest.fixture(scope="session", autouse=True)
async def _indexed():
    from app.modules.agents import load_all

    load_all()
    await ensure_indexes(database())


@pytest.fixture(autouse=True)
async def _clean_collections():
    yield
    db = database()
    for name in [*INDEXES, "locks", "counters"]:
        await db[name].delete_many({})


@pytest.fixture
def db():
    return database()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": APP_ORIGIN}
    ) as c:
        yield c


@pytest.fixture
async def make_client(app):
    """Factory for extra independent clients (separate cookie jars)."""
    clients = []

    def _make(origin: str | None = APP_ORIGIN):
        headers = {"Origin": origin} if origin else {}
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
        )
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()
