"""Deterministic mock outputs for the Solution Architect (tests and offline demos only)."""

import json
import re


def _between(text: str, tag: str):
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else None


def _node(id_, type_, label, tech, reqs=()):
    return {
        "id": id_,
        "type": type_,
        "label": label,
        "description": "",
        "technology": tech,
        "responsibilities": [],
        "config": {},
        "requirement_ids": list(reqs),
        "position": {"x": 0, "y": 0},
        "group": None,
    }


def _edge(id_, s, t, label, protocol=None):
    return {
        "id": id_,
        "source": s,
        "target": t,
        "label": label,
        "protocol": protocol,
        "data": None,
        "requirement_ids": [],
    }


def architecture_output(system: str, user: str) -> dict:
    reqs = json.loads(_between(user, "approved_requirements") or '{"requirements": []}')
    ids = [r["id"] for r in reqs.get("requirements", [])]
    fr = [i for i in ids if i.startswith("FR-")]
    nfr = [i for i in ids if i.startswith("NFR-")]
    return {
        "content": {
            "schema_version": "1.0",
            "diagram_type": "system",
            "nodes": [
                _node("n_users", "client", "Users", "Browser"),
                _node("n_web", "frontend", "Web app", "Next.js", fr),
                _node("n_auth", "auth", "Identity provider", "OIDC"),
                _node("n_api", "backend", "API", "FastAPI", fr),
                _node("n_worker", "worker", "Background worker", "Python"),
                _node("n_db", "database", "Database", "MongoDB"),
                _node("n_monitoring", "monitoring", "Monitoring", "OpenTelemetry"),
            ],
            "edges": [
                _edge("e_users_web", "n_users", "n_web", "uses", "HTTPS"),
                _edge("e_web_auth", "n_web", "n_auth", "sign in", "OIDC"),
                _edge("e_web_api", "n_web", "n_api", "REST", "HTTPS"),
                _edge("e_api_worker", "n_api", "n_worker", "jobs"),
                _edge("e_api_db", "n_api", "n_db", "reads/writes"),
                _edge("e_api_monitoring", "n_api", "n_monitoring", "telemetry"),
            ],
            "groups": [],
            "decisions": [
                {
                    "id": "ADR-001",
                    "title": "Modular monolith with a worker",
                    "decision": "One API service plus a background worker.",
                    "alternatives": ["Microservices"],
                    "rationale": "[Mock] Simplest design that meets the requirements.",
                    "requirement_ids": nfr,
                }
            ],
            "notes": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
        "summary": "[Mock] Modular monolith: web app, API, background worker and database.",
    }


def change_set_output(system: str, user: str) -> dict:
    instruction = (_between(user, "instruction") or "").lower()
    current = json.loads(_between(user, "current_architecture") or '{"nodes": [], "edges": []}')
    node_ids = {n["id"] for n in current.get("nodes", [])}
    anchor = "n_api" if "n_api" in node_ids else next(iter(sorted(node_ids)), None)
    if "redis" in instruction or "cache" in instruction:
        new_id, label, type_, tech = "n_redis", "Redis", "cache", "Redis 7"
    else:
        new_id, label, type_, tech = "n_requested_component", "Requested component", "service", ""
    while new_id in node_ids:
        new_id += "_2"
    ops = [{"op": "add_node", "node": _node(new_id, type_, label, tech)}]
    if anchor:
        ops.append(
            {
                "op": "add_edge",
                "edge": _edge(f"e_{anchor[2:]}_{new_id[2:]}", anchor, new_id, "uses"),
            }
        )
    return {"ops": ops, "summary": f"[Mock] Added {label} connected to the API."}
