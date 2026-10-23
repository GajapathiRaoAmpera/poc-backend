import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.db import engine

TABLES = {
    "users",
    "sessions",
    "password_reset_tokens",
    "login_attempts",
    "projects",
    "project_specifications",
    "audit_logs",
}


def _cfg() -> Config:
    return Config("alembic.ini")


async def _tables() -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        return {r[0] for r in rows}


async def _version() -> str | None:
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()


async def test_upgrade_downgrade_roundtrip():
    await asyncio.to_thread(command.upgrade, _cfg(), "head")
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    assert not (TABLES & await _tables())
    await asyncio.to_thread(command.upgrade, _cfg(), "head")
    assert TABLES <= await _tables()
    assert await _version() == "0001"
