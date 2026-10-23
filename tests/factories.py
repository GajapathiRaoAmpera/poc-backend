from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.auth.models import User, UserRole, UserStatus

DEFAULT_PASSWORD = "correct-horse-1"


async def make_user(
    db: AsyncSession,
    email: str = "ana@example.com",
    password: str = DEFAULT_PASSWORD,
    role: UserRole = UserRole.USER,
    status: UserStatus = UserStatus.ACTIVE,
    name: str = "Ana",
) -> User:
    user = User(
        email=email, name=name, password_hash=hash_password(password), role=role, status=status
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def login(client, email: str, password: str = DEFAULT_PASSWORD) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
