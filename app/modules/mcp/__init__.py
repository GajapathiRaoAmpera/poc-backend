"""Browser automation over MCP. Tests monkeypatch ``get_browser``."""

import os
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from app.core.config import get_settings


class McpBrowser(Protocol):
    async def call(self, tool: str, args: dict) -> str: ...

    async def screenshot(self) -> bytes: ...


def get_browser() -> AbstractAsyncContextManager[McpBrowser]:
    if get_settings().environment == "test" and os.environ.get("AMPERA_FAKE_MCP") == "1":
        from app.modules.mcp.fake import FakeMcp

        @asynccontextmanager
        async def _fake():
            yield FakeMcp({})

        return _fake()
    from app.modules.mcp import playwright

    return playwright.session()
