import base64
import tempfile
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ARGS = ["-y", "@playwright/mcp@latest", "--headless", "--isolated", "--viewport-size", "1280,800"]


class PlaywrightBrowser:
    def __init__(self, session: ClientSession) -> None:
        self._s = session

    async def _call(self, tool: str, args: dict):
        res = await self._s.call_tool(tool, args)
        if res.is_error:
            text = "".join(getattr(c, "text", "") for c in res.content)
            raise RuntimeError(f"{tool} failed: {text[:500]}")
        return res

    async def call(self, tool: str, args: dict) -> str:
        res = await self._call(tool, args)
        return "\n".join(c.text for c in res.content if c.type == "text")

    async def screenshot(self) -> bytes:
        res = await self._call("browser_take_screenshot", {})
        for c in res.content:
            if c.type == "image":
                return base64.b64decode(c.data)
        raise RuntimeError("browser_take_screenshot returned no image")


@asynccontextmanager
async def session():
    # cwd is a throwaway dir: the server writes .playwright-mcp/ output files there.
    with tempfile.TemporaryDirectory() as cwd:
        params = StdioServerParameters(command="npx", args=ARGS, cwd=cwd)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            yield PlaywrightBrowser(s)
