"""Deterministic mock outputs for the UI/UX Design Agent (tests and offline demos only)."""

import re

THEME = {
    "primary": "#4f46e5",
    "surface": "#f8fafc",
    "text": "#0f172a",
    "font_family": "Inter, system-ui, sans-serif",
    "radius": "12px",
}


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def _page(title: str, body: str, link: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><title>' + title + "</title>"
        "<style>:root{--primary:#4f46e5;--surface:#f8fafc;--text:#0f172a;--radius:12px}"
        "body{margin:0;font:16px/1.5 Inter,system-ui,sans-serif;background:var(--surface);"
        "color:var(--text)}header{padding:16px 32px;background:#fff;"
        "border-bottom:1px solid #e2e8f0;"
        "font-weight:600}main{max-width:720px;margin:48px auto;padding:32px;background:#fff;"
        "border:1px solid #e2e8f0;border-radius:var(--radius)}"
        "a{display:inline-block;margin-top:24px;"
        "padding:8px 16px;background:var(--primary);color:#fff;border-radius:8px;"
        "text-decoration:none}</style></head><body><header>Mock app</header><main><h1>"
        + title
        + "</h1>"
        + body
        + link
        + "</main></body></html>"
    )


def design_output(system: str, user: str) -> dict:
    rows = [ln.split(" | ") for ln in _between(user, "requirements").splitlines() if " | " in ln]
    ids = [r[0] for r in rows if r[0].startswith("FR-")]
    items = "".join(f"<li>{r[3].split(':')[0]}</li>" for r in rows if r[0].startswith("FR-"))
    return {
        "summary": "[Mock] A home screen and a detail screen.",
        "theme": THEME,
        "screens": [
            {
                "id": "S-001",
                "name": "Home",
                "purpose": "Entry point",
                "requirement_ids": ids,
                "html": _page("Home", f"<ul>{items}</ul>", '<a href="#S-002">Open detail</a>'),
            },
            {
                "id": "S-002",
                "name": "Detail",
                "purpose": "Item detail",
                "requirement_ids": ids[:1],
                "html": _page("Detail", "<p>[Mock] Detail view.</p>", '<a href="#S-001">Back</a>'),
            },
        ],
        "flows": [
            {"from_screen": "S-001", "action": "Open detail", "to_screen": "S-002"},
            {"from_screen": "S-002", "action": "Back", "to_screen": "S-001"},
        ],
    }


def design_changes(system: str, user: str) -> dict:
    instruction = _between(user, "instruction") or "Mock change"
    return {
        "summary": "[Mock] Updated the home screen.",
        "changes": [
            {
                "op": "write",
                "screen": {
                    "id": "S-001",
                    "name": "Home",
                    "purpose": "Entry point",
                    "requirement_ids": [],
                    "html": _page("Home", f"<p>{instruction[:200]}</p>"),
                },
            }
        ],
    }


def design_chat_output(system: str, user: str) -> dict:
    return {
        "reply": "[Mock] I'll update the screens with that in mind.",
        "action": "revise",
        "instruction": _between(user, "instruction") or None,
    }
