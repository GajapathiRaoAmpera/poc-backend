"""Deterministic mock outputs for the Testing Agent (tests and offline demos only)."""

import re


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def test_plan(system: str, user: str) -> dict:
    rows = [ln.split(" | ") for ln in _between(user, "requirements").splitlines() if " | " in ln]
    must = [r[0] for r in rows if r[1] == "MUST"]
    return {
        "summary": "[Mock] One check per MUST requirement.",
        "cases": [
            {
                "id": f"TC-{i:03d}",
                "requirement_id": rid,
                "title": f"Check {rid}",
                "kind": "ui",
                "steps": ["Open the app", "Look for the feature"],
            }
            for i, rid in enumerate(must, 1)
        ],
    }


def test_step(system: str, user: str) -> dict:
    n = len(re.findall(r"^\d+\. ", _between(user, "history"), re.M))
    origin = (_between(user, "origins").splitlines() or ["http://127.0.0.1"])[0]
    if n == 0:
        return {"tool": "browser_navigate", "args": {"url": origin}}
    if n == 1:
        return {"tool": "browser_snapshot", "args": {}}
    return {"tool": "report_result", "args": {"passed": "true", "evidence": "[Mock] Looks right."}}


def testing_chat_output(system: str, user: str) -> dict:
    return {"reply": "[Mock] I'll run the tests again.", "action": "retest"}
