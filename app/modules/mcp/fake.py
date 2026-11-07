import base64
import inspect
from collections.abc import Callable

# 1x1 transparent PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakeMcp:
    """Scripted McpBrowser: ``script[tool]`` is a string or ``f(args)`` (sync or async)."""

    def __init__(self, script: dict[str, str | Callable] | None = None) -> None:
        self.script = script or {}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, args: dict) -> str:
        self.calls.append((tool, args))
        out = self.script.get(tool, "")
        if callable(out):
            out = out(args)
            if inspect.isawaitable(out):
                out = await out
        return out

    async def screenshot(self) -> bytes:
        self.calls.append(("browser_take_screenshot", {}))
        return PNG_1X1
