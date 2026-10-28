"""Deterministic mock output for the Project Orchestrator (tests and offline demos only).

A first intake message containing "[ask]" yields one blocking question until the client replies.
"""

import json
import re


def _block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def orchestrator_output(system: str, user: str) -> dict:
    messages = re.findall(
        r'<message from="client">\n(.*?)\n</message>', _block(user, "intake_conversation"), re.S
    )
    brief = json.loads(_block(user, "current_brief") or "{}")
    if not brief.get("basics") and messages:
        idea = messages[0]
        brief = {
            "basics": {"name": " ".join(idea.split()[:5])[:160], "description": idea[:5000]},
            "problem": {"business_problem": " ".join(messages)[:5000]},
        }
    name = (brief.get("basics") or {}).get("name") or "New project"
    ask = bool(messages) and "[ask]" in messages[0] and len(messages) == 1
    stage = lambda s, inputs, inc=True: {  # noqa: E731
        "stage": s,
        "include": inc,
        "rationale": "[Mock] " + ("needed" if inc else "no agent yet"),
        "inputs": inputs,
        "approval_required": inc,
    }
    return {
        "content": {
            "project_name": name,
            "objective_summary": (brief.get("basics") or {}).get("description") or name,
            "project_type": "web_app",
            "brief": brief,
            "stages": [
                stage("requirements", []),
                stage("architecture", ["requirements"]),
                stage("plan", ["requirements", "architecture"]),
                stage("budget", ["plan", "architecture"]),
                stage("code", [], False),
            ],
            "missing_info": [
                {
                    "id": "I-001",
                    "question": "Who will use this, and roughly how many people?",
                    "why_it_matters": "Scale and roles shape the requirements",
                    "blocking": ask,
                }
            ],
            "next_action": "ask_user" if ask else "run_stage",
            "next_stage": None if ask else "requirements",
            "decision_summary": "[Mock] Requirements, architecture, then plan and budget.",
        },
        "summary": "[Mock] Planned requirements, architecture, plan and budget.",
    }
