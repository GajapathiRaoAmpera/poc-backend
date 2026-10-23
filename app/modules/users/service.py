import re
import uuid
from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from app.core.db import Db, in_transaction
from app.core.documents import now
from app.core.errors import Conflict, NotFound
from app.core.pagination import _PageParams
from app.core.security import hash_password, validate_password
from app.modules.audit.service import record_audit
from app.modules.auth.models import User, UserRole, UserStatus
from app.modules.auth.service import revoke_sessions
from app.modules.users.schemas import AdminUserCreate, AdminUserUpdate

LAST_ADMIN = "You can't remove the last active Super Admin"
ADMIN_GUARD_ID = "super_admins"


async def list_users(
    db: Db,
    params: _PageParams,
    q: str | None,
    role: UserRole | None,
    status: UserStatus | None,
) -> tuple[list[User], int]:
    query: dict[str, Any] = {}
    if q and q.strip():
        pattern = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"name": pattern}, {"email": pattern}]
    if role:
        query["role"] = role.value
    if status:
        query["status"] = status.value
    total = await db.users.count_documents(query)
    cursor = (
        db.users.find(query)
        .sort([("created_at", 1), ("email", 1)])
        .skip(params.offset)
        .limit(params.page_size)
    )
    return [User.from_doc(d) async for d in cursor], total


async def get_user(db: Db, user_id: uuid.UUID, session: AsyncClientSession | None = None) -> User:
    doc = await db.users.find_one({"_id": user_id}, session=session)
    if doc is None:
        raise NotFound("User not found")
    return User.from_doc(doc)


async def create_user(db: Db, actor: User, data: AdminUserCreate) -> User:
    validate_password(data.password, data.email)
    user = User(
        email=data.email, name=data.name, role=data.role, password_hash=hash_password(data.password)
    )
    try:
        await db.users.insert_one(user.to_doc())
    except DuplicateKeyError as exc:
        raise Conflict("An account with this email already exists") from exc
    await record_audit(db, actor.id, "user.created", "user", user.id, {"role": user.role.value})
    return user


async def _guard_admins(db: Db, session: AsyncClientSession) -> int:
    """Count active admins after writing a shared guard document.

    Two concurrent demotions each change a *different* user, so without a common write
    MongoDB would let both commit (write skew) and leave zero admins. Writing the guard
    makes them conflict; ``with_transaction`` retries the loser, which then sees the new count.
    """
    await db.locks.update_one(
        {"_id": ADMIN_GUARD_ID}, {"$inc": {"v": 1}}, upsert=True, session=session
    )
    return await db.users.count_documents(
        {"role": UserRole.SUPER_ADMIN.value, "status": UserStatus.ACTIVE.value}, session=session
    )


def _is_active_admin(user: User) -> bool:
    return user.role == UserRole.SUPER_ADMIN and user.status == UserStatus.ACTIVE


def _plain(v: object) -> object:
    return v.value if isinstance(v, UserRole | UserStatus) else v


async def update_user(db: Db, actor: User, user_id: uuid.UUID, data: AdminUserUpdate) -> User:
    fields = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    # name/role/status are required on the document: an explicit null means "leave as is".
    for key in ("name", "role", "status"):
        if key in fields and fields[key] is None:
            del fields[key]

    async def txn(session: AsyncClientSession) -> tuple[User, dict]:
        user = await get_user(db, user_id, session)
        loses_admin = fields.get("role") == UserRole.USER or (
            fields.get("status") == UserStatus.DISABLED
        )
        if loses_admin and _is_active_admin(user) and await _guard_admins(db, session) <= 1:
            raise Conflict(LAST_ADMIN)
        changes = {}
        for key, value in fields.items():
            old = getattr(user, key)
            if old != value:
                changes[key] = {"from": _plain(old), "to": _plain(value)}
                setattr(user, key, value)
        if changes:
            user.updated_at = now()
            doc = user.to_doc()
            await db.users.update_one(
                {"_id": user.id},
                {"$set": {k: doc[k] for k in (*changes, "updated_at")}},
                session=session,
            )
            await record_audit(
                db, actor.id, "user.updated", "user", user.id, {"changes": changes}, session
            )
        return user, changes

    user, changes = await in_transaction(db, txn)
    if user.status == UserStatus.DISABLED and "status" in changes:
        await revoke_sessions(db, user.id)
    return user


async def delete_user(db: Db, actor: User, user_id: uuid.UUID) -> None:
    if user_id == actor.id:
        raise Conflict("You can't delete your own account")

    async def txn(session: AsyncClientSession) -> None:
        user = await get_user(db, user_id, session)
        if _is_active_admin(user) and await _guard_admins(db, session) <= 1:
            raise Conflict(LAST_ADMIN)
        # No foreign keys in MongoDB: cascade explicitly, all-or-nothing.
        project_ids = await db.projects.distinct("_id", {"owner_id": user.id}, session=session)
        await db.project_specifications.delete_many(
            {"project_id": {"$in": project_ids}}, session=session
        )
        await db.projects.delete_many({"owner_id": user.id}, session=session)
        await db.sessions.delete_many({"user_id": user.id}, session=session)
        await db.password_reset_tokens.delete_many({"user_id": user.id}, session=session)
        await db.users.delete_one({"_id": user.id}, session=session)
        await record_audit(
            db, actor.id, "user.deleted", "user", user.id, {"email": user.email}, session
        )

    await in_transaction(db, txn)
