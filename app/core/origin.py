from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import error_response

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class OriginCheckMiddleware:
    """CSRF defence: state-changing requests must come from the app's own origin."""

    def __init__(self, app: ASGIApp, allowed_origin: str) -> None:
        self.app = app
        self.allowed_origin = allowed_origin.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            origin = dict(scope["headers"]).get(b"origin", b"").decode().rstrip("/")
            if origin != self.allowed_origin:
                response = error_response(403, "FORBIDDEN", "Request origin not allowed")
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
