import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import install_error_handlers
from app.core.origin import OriginCheckMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.projects.router import router as projects_router
from app.modules.users.router import router as users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        # Never print the URL itself: it contains the database password.
        raise SystemExit(
            "Cannot connect to the database. Check DATABASE_URL "
            "(Supabase: use the direct or session-pooler string, port 5432)."
        ) from exc
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Ampera Project Delivery API", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(OriginCheckMiddleware, allowed_origin=get_settings().app_origin)

    api = APIRouter(prefix="/api/v1")

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    api.include_router(auth_router)
    api.include_router(users_router)
    api.include_router(projects_router)
    app.include_router(api)
    return app


app = create_app()
