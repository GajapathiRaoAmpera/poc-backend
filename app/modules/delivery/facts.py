"""Deterministic facts about a project's delivery state, read fresh from the database.

The router and the orchestrator both decide from these, never from the checkpoint alone,
so a decision always reflects approvals, edits and staleness made outside the graph.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.db import Db
from app.modules.artifacts import service as artifacts
from app.modules.projects.models import Project

ARTIFACT_STAGES = ("requirements", "architecture")
MAX_DIGEST = 4000


@dataclass
class StageFacts:
    stage: str
    has_draft: bool = False
    latest_revision: int | None = None
    approved_revision: int | None = None
    approved_is_latest: bool = False
    stale: bool = False
    stale_reason: str | None = None
    save_errors: int = 0
    blocking_unanswered: int = 0
    changes_requested: str | None = None
    # The user answered questions after the agent's last revision → the agent should revise.
    answered_since_agent: bool = False
    active_run: bool = False
    summary: str = ""


@dataclass
class Facts:
    project_id: uuid.UUID
    spec_revision: int | None
    stages: dict[str, StageFacts]
    intake_messages: list[str] = field(default_factory=list)
    preferences: dict[str, Any] = field(default_factory=dict)


def _answered(content: dict[str, Any]) -> set[str]:
    return {q["id"] for q in content.get("open_questions", []) if (q.get("answer") or "").strip()}


async def _stage_facts(db: Db, project: Project, stage: str) -> StageFacts:
    f = StageFacts(stage=stage)
    f.active_run = bool(
        await db.agent_runs.count_documents(
            {"project_id": project.id, "stage": stage, "active": True}, limit=1
        )
    )
    art = await artifacts.get_artifact(db, project, stage)
    if art is None or not art.latest_revision:
        return f
    rev = await artifacts.latest_revision(db, project, stage)
    f.has_draft = True
    f.latest_revision = art.latest_revision
    f.approved_revision = art.approved_revision
    f.approved_is_latest = art.approved_revision == art.latest_revision
    f.stale, f.stale_reason = art.stale, art.stale_reason
    # Unanswered blocking questions are stored as approval errors; they are counted separately.
    f.save_errors = sum(
        1
        for i in rev.validation.issues
        if i.severity == "error" and i.code != "UNANSWERED_QUESTION"
    )
    f.blocking_unanswered = sum(
        1
        for q in rev.content.get("open_questions", [])
        if q.get("blocking") and not (q.get("answer") or "").strip()
    )
    f.summary = (rev.summary or "")[:200]
    if rev.origin == "user":
        agent_rev = await db.artifact_revisions.find_one(
            {"project_id": project.id, "kind": stage, "origin": "agent"},
            sort=[("revision", -1)],
            projection={"content.open_questions": 1},
        )
        before = _answered(agent_rev["content"]) if agent_rev else set()
        f.answered_since_agent = bool(_answered(rev.content) - before)
    decision = await artifacts.latest_decision(db, project, stage)
    if (
        decision
        and decision.decision == "CHANGES_REQUESTED"
        and decision.revision == art.latest_revision
    ):
        f.changes_requested = decision.comment or "Changes requested"
    return f


async def collect_facts(db: Db, project: Project) -> Facts:
    spec = await db.project_specifications.find_one(
        {"project_id": project.id}, sort=[("revision", -1)], projection={"revision": 1}
    )
    doc = await db.projects.find_one({"_id": project.id}, {"preferences": 1})
    messages = [
        m["text"]
        async for m in db.messages.find(
            {"project_id": project.id, "channel": "intake", "role": "user"}
        ).sort("seq", 1)
    ]
    return Facts(
        project_id=project.id,
        spec_revision=spec["revision"] if spec else None,
        stages={s: await _stage_facts(db, project, s) for s in ARTIFACT_STAGES},
        intake_messages=messages,
        preferences=(doc or {}).get("preferences") or {},
    )


def _describe(f: StageFacts) -> str:
    if not f.has_draft:
        return f"{f.stage}: not started" + (" (agent running)" if f.active_run else "")
    parts = [f"{f.stage}: draft r{f.latest_revision}"]
    parts.append("approved" if f.approved_is_latest else "not approved")
    if f.stale:
        parts.append(f"STALE ({f.stale_reason})")
    if f.blocking_unanswered:
        parts.append(f"{f.blocking_unanswered} blocking question(s) unanswered")
    if f.save_errors:
        parts.append(f"{f.save_errors} validation error(s)")
    if f.answered_since_agent:
        parts.append("new answers since the agent's last draft")
    if f.changes_requested:
        parts.append(f"changes requested: {f.changes_requested[:300]}")
    if f.active_run:
        parts.append("agent running")
    if f.summary:
        parts.append(f"summary: {f.summary}")
    return ", ".join(parts)


def digest(facts: Facts) -> str:
    """Compact state for the orchestrator prompt. Never includes artifact content."""
    lines = [f"specification revision: {facts.spec_revision or 'none'}"]
    lines += [_describe(f) for f in facts.stages.values()]
    if facts.preferences:
        lines.append("preferences: " + json.dumps(facts.preferences, default=str)[:800])
    return "\n".join(lines)[:MAX_DIGEST]
