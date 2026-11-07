import base64

import pytest

from app.modules import mcp
from app.modules.mcp.fake import FakeMcp


async def test_fake_records_calls():
    fake = FakeMcp({"browser_navigate": "ok", "browser_snapshot": lambda a: f"snap {a['depth']}"})
    assert await fake.call("browser_navigate", {"url": "u"}) == "ok"
    assert await fake.call("browser_snapshot", {"depth": 2}) == "snap 2"
    assert (await fake.screenshot()).startswith(b"\x89PNG")
    assert [c[0] for c in fake.calls] == [
        "browser_navigate",
        "browser_snapshot",
        "browser_take_screenshot",
    ]


async def test_get_browser_fake_only_in_test_env(monkeypatch):
    monkeypatch.setenv("AMPERA_FAKE_MCP", "1")
    async with mcp.get_browser() as b:
        assert isinstance(b, FakeMcp)


@pytest.mark.browser
async def test_real_screenshot_of_data_url():
    from app.modules.mcp import playwright

    url = "data:text/html;base64," + base64.b64encode(b"<h1>Hi</h1>").decode()
    async with playwright.session() as b:
        await b.call("browser_navigate", {"url": url})
        assert (await b.screenshot()).startswith(b"\x89PNG")
