"""Operator commands.

    uv run python -m app.cli init-db
    uv run python -m app.cli create-superadmin --email you@example.com [--name NAME]

The password is prompted for twice (never passed on the command line). Use
``--password-stdin`` for non-interactive setups such as test bootstrapping.
"""

import argparse
import asyncio
import sys
from getpass import getpass

from pymongo.errors import DuplicateKeyError

from app.core.db import database, ensure_indexes, make_client
from app.core.errors import AppError
from app.core.security import hash_password, validate_password
from app.modules.audit.service import record_audit
from app.modules.auth.models import User, UserRole
from app.modules.auth.schemas import RegisterIn


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass("Password: ")
    if getpass("Repeat password: ") != first:
        raise AppError("VALIDATION_ERROR", "Passwords do not match", 422)
    return first


async def _create_superadmin(email: str, name: str, password: str) -> None:
    # A private client: this may run in its own event loop (e.g. from a thread).
    client = make_client()
    try:
        db = database(client)
        await ensure_indexes(db)
        user = User(
            email=email, name=name, role=UserRole.SUPER_ADMIN, password_hash=hash_password(password)
        )
        await db.users.insert_one(user.to_doc())
        await record_audit(db, None, "user.bootstrap_superadmin", "user", user.id)
    finally:
        await client.close()


async def _init_db() -> None:
    client = make_client()
    try:
        await ensure_indexes(database(client))
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-superadmin", help="Create a Super Admin account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="Super Admin")
    create.add_argument("--password-stdin", action="store_true")
    sub.add_parser("init-db", help="Create MongoDB indexes (idempotent)")
    args = parser.parse_args(argv)

    if args.command == "init-db":
        asyncio.run(_init_db())
        print("Indexes are up to date.")
        return 0

    try:
        password = _read_password(args.password_stdin)
        # Reuse the registration schema for email/name normalisation and validation.
        data = RegisterIn(name=args.name, email=args.email, password=password)
        validate_password(data.password, data.email)
        asyncio.run(_create_superadmin(data.email, data.name, data.password))
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except DuplicateKeyError:
        print("Error: an account with this email already exists", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Super Admin {data.email} created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
