"""Tester tools: every URL must stay on a sandbox origin; results are truncated before the LLM."""

from urllib.parse import urlsplit

import httpx

from app.modules.agents.testing.schema import MAX_RESULT_CHARS, Step
from app.modules.mcp import McpBrowser

URL_TOOLS = {"browser_navigate", "http_request"}


def origin(url: str) -> str | None:
    try:
        p = urlsplit(url)
        port = p.port or {"http": 80, "https": 443}.get(p.scheme)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    return f"{p.scheme}://{p.hostname.lower()}:{port}"


def guard(step: Step, allowed_origins: set[str]) -> str | None:
    """A refusal message for a URL outside the sandbox, else None."""
    if step.tool not in URL_TOOLS:
        return None
    url = step.args.get("url", "")
    if origin(url) not in {origin(o) for o in allowed_origins}:
        allowed = ", ".join(sorted(allowed_origins)) or "none"
        return f"Refused: {url[:200]!r} is outside the sandbox. Allowed origins: {allowed}"
    return None


async def _http(args: dict[str, str]) -> str:
    method = args.get("method", "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return f"Refused: unsupported method {method[:20]}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        resp = await client.request(
            method, args["url"], content=args.get("body"), headers=_headers(args)
        )
    return f"HTTP {resp.status_code}\n{resp.text}"


def _headers(args: dict[str, str]) -> dict[str, str]:
    return {"content-type": args["content_type"]} if "content_type" in args else {}


async def run_tool(step: Step, browser: McpBrowser, allowed_origins: set[str]) -> str:
    """Execute one non-verdict step. Never raises; the text goes back to the model (<=3000)."""
    if (refusal := guard(step, allowed_origins)) is not None:
        return refusal[:MAX_RESULT_CHARS]
    try:
        if step.tool == "http_request":
            out = await _http(step.args)
        elif step.tool == "browser_take_screenshot":
            await browser.screenshot()
            out = "Screenshot taken"
        else:
            args = dict(step.args)
            if "submit" in args:
                args["submit"] = args["submit"].lower() == "true"  # type: ignore[assignment]
            out = await browser.call(step.tool, args)
    except Exception as exc:  # a failing tool is feedback for the model, not a crash
        out = f"Tool error: {str(exc)[:500]}"
    return out[:MAX_RESULT_CHARS]
