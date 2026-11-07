"""Deterministic mock outputs for the Development Agent (tests and offline demos only)."""

import re


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def code_output(system: str, user: str) -> dict:
    comps = [ln.split(" | ") for ln in _between(user, "components").splitlines() if " | " in ln]
    files = [
        {
            "path": "README.md",
            "content": "# Mock scaffold\n\n[Mock] Generated offline by the mock provider.\n",
        },
        {"path": ".env.example", "content": "APP_ENV=development\n"},
        {
            "path": "scripts/dev.sh",
            "content": '#!/usr/bin/env bash\nset -euo pipefail\necho "Starting $1"\n',
            "executable": True,
        },
    ]
    for c in comps[:8]:
        folder = re.sub(r"[^a-z0-9-]", "", c[0].lower().replace("_", "-")) or "component"
        files.append({"path": f"{folder}/README.md", "content": f"# {c[2]}\n\nTODO: {c[1]}\n"})
        # A manifest makes the folder a runnable component for the Implementation stage.
        files.append({"path": f"{folder}/requirements.txt", "content": "# [Mock] no deps\n"})
    return {
        "summary": "[Mock] One folder per component, a README and a dev script.",
        "project_slug": "mock-project",
        "files": files,
        "next_steps": ["Read README.md"],
    }


def code_changes(system: str, user: str) -> dict:
    instruction = _between(user, "instruction") or "Mock change"
    # The Implementation Agent scopes each call to one folder.
    folder = re.search(r"Component folder: (\S+?)/|\(folder (\S+?)/\)", user)
    prefix = next((g + "/" for g in folder.groups() if g), "") if folder else ""
    return {
        "summary": "[Mock] Added NOTES.md.",
        "changes": [{"op": "write", "path": f"{prefix}NOTES.md", "content": f"{instruction}\n"}],
    }


def code_chat_output(system: str, user: str) -> dict:
    return {
        "reply": "[Mock] I'll update the scaffold with that in mind.",
        "action": "revise",
        "instruction": _between(user, "instruction") or None,
    }
