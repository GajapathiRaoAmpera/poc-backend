"""Operator commands.

    uv run python -m app.cli create-superadmin --email you@example.com [--name NAME]

The password is prompted for twice (never passed on the command line). Use
``--password-stdin`` for non-interactive setups such as test bootstrapping.
"""

import argparse
import asyncio
import sys
from getpass import getpass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_password, validate_password
from app.models import AuditLog, User
from app.modules.auth.models import UserRole
from app.modules.auth.schemas import RegisterIn


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass("Password: ")
    if getpass("Repeat password: ") != first:
        raise AppError("VALIDATION_ERROR", "Passwords do not match", 422)
    return first


async def _create_superadmin(email: str, name: str, password: str) -> None:
    # A private engine: this may run in its own event loop (e.g. from a thread).
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine)() as db:
            user = User(
                email=email,
                name=name,
                role=UserRole.SUPER_ADMIN,
                password_hash=hash_password(password),
            )
            db.add(user)
            await db.flush()
            db.add(
                AuditLog(
                    actor_id=None,
                    action="user.bootstrap_superadmin",
                    target_type="user",
                    target_id=user.id,
                )
            )
            await db.commit()
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-superadmin", help="Create a Super Admin account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="Super Admin")
    create.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args(argv)

    try:
        password = _read_password(args.password_stdin)
        # Reuse the registration schema for email/name normalisation and validation.
        data = RegisterIn(name=args.name, email=args.email, password=password)
        validate_password(data.password, data.email)
        asyncio.run(_create_superadmin(data.email, data.name, data.password))
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except IntegrityError:
        print("Error: an account with this email already exists", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Super Admin {data.email} created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
