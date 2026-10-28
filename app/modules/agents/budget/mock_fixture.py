"""Deterministic mock outputs for the Budget Estimator (tests and offline demos only)."""

import re


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def budget_output(system: str, user: str) -> dict:
    comps = _between(user, "components").splitlines()[1:]
    risks = [ln.split(" | ")[0] for ln in _between(user, "risks").splitlines() if ln]
    return {
        "summary": "[Mock] Hosting for the first component.",
        "items": [
            {
                "component_id": comps[0].split(" | ")[0],
                "category": "infra",
                "description": "Hosting",
                "recurrence": "monthly",
                "amount": 100,
            }
        ]
        if comps
        else [],
        "contingency_pct": 10 if risks else 0,
        "contingency_reason": f"Covers {risks[0]}" if risks else "No plan risks recorded.",
    }


def budget_chat_output(system: str, user: str) -> dict:
    return {
        "reply": "[Mock] I'll revise the budget with that in mind.",
        "action": "revise",
        "instruction": _between(user, "instruction") or None,
    }
