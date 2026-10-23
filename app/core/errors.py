import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.cookies import SESSION_COOKIE

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


class NotFound(AppError):
    def __init__(self, message: str = "Not found", details: Any = None) -> None:
        super().__init__("NOT_FOUND", message, 404, details)


class Forbidden(AppError):
    def __init__(self, message: str = "You don't have access to this", details: Any = None):
        super().__init__("FORBIDDEN", message, 403, details)


class Unauthenticated(AppError):
    def __init__(self, message: str = "Please sign in", details: Any = None) -> None:
        super().__init__("UNAUTHENTICATED", message, 401, details)


class Conflict(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__("CONFLICT", message, 409, details)


class RateLimited(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__("RATE_LIMITED", message, 429, details)


class ValidationFailed(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__("VALIDATION_ERROR", message, 422, details)


_HTTP_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "NOT_FOUND",
    409: "CONFLICT",
    429: "RATE_LIMITED",
}


def error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        response = error_response(exc.status, exc.code, exc.message, exc.details)
        if exc.status == 401 and SESSION_COOKIE in request.cookies:
            # Drop a stale/revoked cookie so clients don't keep presenting it
            # (the web app's optimistic redirect would otherwise loop).
            response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Some fields are invalid", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = 404 if exc.status_code == 405 else exc.status_code
        code = _HTTP_CODES.get(exc.status_code, "INTERNAL")
        message = "Not found" if status == 404 else str(exc.detail)
        return error_response(status, code, message)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return error_response(500, "INTERNAL", "Something went wrong")


class RevisionConflict(AppError):
    def __init__(self, latest_revision: int) -> None:
        super().__init__(
            "REVISION_CONFLICT",
            "This was changed since you loaded it. Reload to see the latest revision.",
            409,
            {"latest_revision": latest_revision},
        )


class NotApprovable(AppError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("NOT_APPROVABLE", reasons[0], 409, {"reasons": reasons})


class StageBlocked(AppError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("STAGE_BLOCKED", reasons[0], 409, {"reasons": reasons})


class QuotaExceeded(AppError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            "QUOTA_EXCEEDED",
            f"Daily limit of {limit} agent runs reached. Try again later or ask a Super Admin.",
            429,
            {"limit": limit},
        )
