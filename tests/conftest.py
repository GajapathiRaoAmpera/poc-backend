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


@pytest.fixture(scope="session", autouse=True)
async def _migrated():
    import asyncio

    from alembic import command
    from alembic.config import Config

    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield
    from sqlalchemy import text

    from app.core.db import engine
    from app.models import Base

    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def db():
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        yield session
