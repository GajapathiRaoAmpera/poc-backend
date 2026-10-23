import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.pagination import _PageParams
from app.core.security import hash_password, validate_password
from app.modules.audit.service import record_audit
from app.modules.auth.models import User, UserRole, UserStatus
from app.modules.auth.service import revoke_sessions
from app.modules.users.schemas import AdminUserCreate, AdminUserUpdate

LAST_ADMIN = "You can't remove the last active Super Admin"


async def list_users(
    db: AsyncSession,
    params: _PageParams,
    q: str | None,
    role: UserRole | None,
    status: UserStatus | None,
) -> tuple[list[User], int]:
    stmt = select(User)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.name.ilike(like), User.email.ilike(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if status:
        stmt = stmt.where(User.status == status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = await db.execute(
        stmt.order_by(User.created_at, User.email).offset(params.offset).limit(params.page_size)
    )
    return list(rows.scalars()), total


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return user


async def create_user(db: AsyncSession, actor: User, data: AdminUserCreate) -> User:
    validate_password(data.password, data.email)
    user = User(
        email=data.email, name=data.name, role=data.role, password_hash=hash_password(data.password)
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("An account with this email already exists") from exc
    await record_audit(db, actor.id, "user.created", "user", user.id, {"role": user.role.value})
    await db.commit()
    await db.refresh(user)
    return user


async def _is_last_active_admin(db: AsyncSession, user: User) -> bool:
    if user.role != UserRole.SUPER_ADMIN or user.status != UserStatus.ACTIVE:
        return False
    # Lock every active admin row so two concurrent demotions can't both pass the check.
    admins = await db.execute(
        select(User.id)
        .where(User.role == UserRole.SUPER_ADMIN, User.status == UserStatus.ACTIVE)
        .with_for_update()
    )
    return len(admins.all()) <= 1


async def update_user(
    db: AsyncSession, actor: User, user_id: uuid.UUID, data: AdminUserUpdate
) -> User:
    user = await get_user(db, user_id)
    fields = data.model_dump(exclude_unset=True)
    loses_admin = fields.get("role") == UserRole.USER or fields.get("status") == UserStatus.DISABLED
    if loses_admin and await _is_last_active_admin(db, user):
        raise Conflict(LAST_ADMIN)

    changes = {}
    for key, value in fields.items():
        old = getattr(user, key)
        if old != value:
            changes[key] = {"from": _plain(old), "to": _plain(value)}
            setattr(user, key, value)
    if changes:
        await record_audit(db, actor.id, "user.updated", "user", user.id, {"changes": changes})
    await db.commit()
    if user.status == UserStatus.DISABLED and "status" in changes:
        await revoke_sessions(db, user.id)
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, actor: User, user_id: uuid.UUID) -> None:
    user = await get_user(db, user_id)
    if user.id == actor.id:
        raise Conflict("You can't delete your own account")
    if await _is_last_active_admin(db, user):
        raise Conflict(LAST_ADMIN)
    await record_audit(db, actor.id, "user.deleted", "user", user.id, {"email": user.email})
    await db.delete(user)
    await db.commit()


def _plain(v: object) -> object:
    return v.value if isinstance(v, UserRole | UserStatus) else v
