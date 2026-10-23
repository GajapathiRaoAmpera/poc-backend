import asyncio

from sqlalchemy import select

from app import cli
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User, UserRole


async def test_cli_creates_superadmin(monkeypatch, db):
    answers = iter(["s3cret-password-1", "s3cret-password-1"])
    monkeypatch.setattr(cli, "getpass", lambda prompt="": next(answers))
    code = await asyncio.to_thread(
        cli.main, ["create-superadmin", "--email", "Root@X.com", "--name", "Root"]
    )
    assert code == 0
    user = (await db.execute(select(User))).scalar_one()
    assert user.email == "root@x.com"
    assert user.role == UserRole.SUPER_ADMIN
    actions = (await db.execute(select(AuditLog.action))).scalars().all()
    assert actions == ["user.bootstrap_superadmin"]


async def test_cli_mismatched_passwords(monkeypatch, db):
    answers = iter(["s3cret-password-1", "different-password"])
    monkeypatch.setattr(cli, "getpass", lambda prompt="": next(answers))
    code = await asyncio.to_thread(cli.main, ["create-superadmin", "--email", "root@x.com"])
    assert code != 0
    assert (await db.execute(select(User))).first() is None


async def test_cli_password_stdin(monkeypatch, db):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("s3cret-password-1\n"))
    code = await asyncio.to_thread(
        cli.main, ["create-superadmin", "--email", "root@x.com", "--password-stdin"]
    )
    assert code == 0
    assert (await db.execute(select(User.role))).scalar_one() == UserRole.SUPER_ADMIN


async def test_cli_weak_password_rejected(monkeypatch, db):
    monkeypatch.setattr(cli, "getpass", lambda prompt="": "short")
    code = await asyncio.to_thread(cli.main, ["create-superadmin", "--email", "root@x.com"])
    assert code != 0
