"""Deterministic mock outputs for the Project Planner (tests and offline demos only)."""

import re


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def plan_output(system: str, user: str) -> dict:
    reqs = [ln.split(" | ") for ln in _between(user, "requirements").splitlines()]
    comps = [ln.split(" | ")[0] for ln in _between(user, "components").splitlines()]
    roles = [r.strip() for r in _between(user, "roles").split(",") if r.strip()] or ["developer"]
    must = [r[0] for r in reqs if len(r) > 1 and r[1] == "MUST"] or [r[0] for r in reqs[:1] if r]
    tasks = [
        {
            "id": f"T-{i:03d}",
            "title": f"Deliver {rid}",
            "milestone_id": "M-01",
            "role": roles[i % len(roles)],
            "effort_days": 2.0,
            "depends_on": [f"T-{i - 1:03d}"] if i > 1 and i % 2 == 0 else [],
            "requirement_ids": [rid],
            "component_ids": comps[:1],
        }
        for i, rid in enumerate(must, 1)
    ]
    return {
        "summary": "[Mock] One milestone delivering each MUST requirement.",
        "milestones": [{"id": "M-01", "title": "Delivery", "goal": "Deliver the MUST scope."}],
        "tasks": tasks,
        "risks": [],
    }


def plan_chat_output(system: str, user: str) -> dict:
    instruction = _between(user, "instruction")
    return {
        "reply": "[Mock] I'll revise the plan with that in mind.",
        "action": "revise",
        "instruction": instruction or None,
    }
