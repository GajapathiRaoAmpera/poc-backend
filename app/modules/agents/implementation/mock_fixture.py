"""Deterministic mock outputs for the Implementation Agent (tests and offline demos only)."""

import re


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def sandbox_spec(system: str, user: str) -> dict:
    rows = [ln.split(" | ") for ln in _between(user, "folders").splitlines() if " | " in ln]
    services = []
    for i, (folder, manifest) in enumerate(rows[:4]):
        py = manifest != "package.json"
        services.append(
            {
                "name": re.sub(r"[^a-z0-9-]", "", folder.lower()) or f"svc{i}",
                "dir": folder,
                "runtime": "python3.12" if py else "node22",
                "install": ["pip", "install", "-r", "requirements.txt"] if py else ["npm", "ci"],
                "start": ["python", "main.py"] if py else ["npm", "start"],
                "port": 8000 + i,
                "health": "/health",
            }
        )
    return {"services": services}


def implementation_chat_output(system: str, user: str) -> dict:
    return {
        "reply": "[Mock] I'll rework the code with that in mind.",
        "action": "revise",
        "instruction": _between(user, "instruction") or None,
    }
