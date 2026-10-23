import os

os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:54329/ampera_test"
)
os.environ["APP_ORIGIN"] = "http://localhost:3000"

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.main import create_app  # noqa: E402

APP_ORIGIN = os.environ["APP_ORIGIN"]


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
