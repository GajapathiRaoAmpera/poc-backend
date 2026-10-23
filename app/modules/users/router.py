import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import DbSession, SuperAdmin
from app.core.pagination import Page, PageParams
from app.modules.auth.models import User, UserRole, UserStatus
from app.modules.users import service
from app.modules.users.schemas import AdminUserCreate, AdminUserOut, AdminUserUpdate

router = APIRouter(prefix="/admin/users", tags=["admin"])


@router.get("", response_model=Page[AdminUserOut])
async def list_users(
    admin: SuperAdmin,
    db: DbSession,
    params: PageParams,
    q: str | None = None,
    role: UserRole | None = None,
    status: UserStatus | None = None,
) -> Page[AdminUserOut]:
    users, total = await service.list_users(db, params, q, role, status)
    return Page(
        items=[AdminUserOut.model_validate(u) for u in users],
        page=params.page,
        page_size=params.page_size,
        total=total,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AdminUserOut)
async def create_user(data: AdminUserCreate, admin: SuperAdmin, db: DbSession) -> User:
    return await service.create_user(db, admin, data)


@router.get("/{user_id}", response_model=AdminUserOut)
async def get_user(user_id: uuid.UUID, admin: SuperAdmin, db: DbSession) -> User:
    return await service.get_user(db, user_id)


@router.patch("/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: uuid.UUID, data: AdminUserUpdate, admin: SuperAdmin, db: DbSession
) -> User:
    return await service.update_user(db, admin, user_id, data)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, admin: SuperAdmin, db: DbSession) -> Response:
    await service.delete_user(db, admin, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
