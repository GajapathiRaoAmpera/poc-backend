import asyncio

from app import cli
from app.modules.auth.models import UserRole


async def test_cli_creates_superadmin(monkeypatch, db):
    answers = iter(["s3cret-password-1", "s3cret-password-1"])
    monkeypatch.setattr(cli, "getpass", lambda prompt="": next(answers))
    code = await asyncio.to_thread(
        cli.main, ["create-superadmin", "--email", "Root@X.com", "--name", "Root"]
    )
    assert code == 0
    users = await db.users.find({}).to_list()
    assert len(users) == 1
    assert users[0]["email"] == "root@x.com"
    assert users[0]["role"] == UserRole.SUPER_ADMIN
    actions = [d["action"] async for d in db.audit_logs.find({})]
    assert actions == ["user.bootstrap_superadmin"]


async def test_cli_mismatched_passwords(monkeypatch, db):
    answers = iter(["s3cret-password-1", "different-password"])
    monkeypatch.setattr(cli, "getpass", lambda prompt="": next(answers))
    code = await asyncio.to_thread(cli.main, ["create-superadmin", "--email", "root@x.com"])
    assert code != 0
    assert await db.users.count_documents({}) == 0


async def test_cli_password_stdin(monkeypatch, db):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("s3cret-password-1\n"))
    code = await asyncio.to_thread(
        cli.main, ["create-superadmin", "--email", "root@x.com", "--password-stdin"]
    )
    assert code == 0
    assert (await db.users.find_one({}))["role"] == UserRole.SUPER_ADMIN


async def test_cli_weak_password_rejected(monkeypatch, db):
    monkeypatch.setattr(cli, "getpass", lambda prompt="": "short")
    code = await asyncio.to_thread(cli.main, ["create-superadmin", "--email", "root@x.com"])
    assert code != 0
