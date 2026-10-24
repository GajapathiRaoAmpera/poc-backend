import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import get_settings
from app.core.db import close_client, database, ensure_indexes
from app.core.errors import install_error_handlers
from app.core.origin import OriginCheckMiddleware
from app.modules.agents import load_all
from app.modules.artifacts.router import router as artifacts_router
from app.modules.auth.router import router as auth_router
from app.modules.changesets.router import router as changesets_router
from app.modules.conversations.router import router as conversations_router
from app.modules.delivery.router import router as delivery_router
from app.modules.orchestration.router import router as workflow_router
from app.modules.projects.router import router as projects_router
from app.modules.users.router import router as users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        db = database()
        await db.command("ping")
        await ensure_indexes(db)
    except Exception as exc:
        # Never print the URI itself: it contains the database password.
        raise SystemExit(
            "Cannot connect to MongoDB. Check MONGODB_URI, and in Atlas allow this machine's "
            "IP under Network Access."
        ) from exc
    yield
    await close_client()


def create_app() -> FastAPI:
    load_all()
    app = FastAPI(title="Ampera Project Delivery API", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    settings = get_settings()
    app.add_middleware(OriginCheckMiddleware, allowed_origin=settings.app_origin)
    if settings.trusted_proxies:
        # Real client IPs (login throttling, session records) when behind the Next.js proxy.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)

    api = APIRouter(prefix="/api/v1")

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    api.include_router(auth_router)
    api.include_router(users_router)
    api.include_router(delivery_router)
    api.include_router(projects_router)
    api.include_router(workflow_router)
    api.include_router(artifacts_router)
    api.include_router(changesets_router)
    api.include_router(conversations_router)
    app.include_router(api)
    return app


app = create_app()
