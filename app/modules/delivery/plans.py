"""Versioned orchestrator plans (``plans``). Append-only: never updated after insert."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field
from pymongo.errors import DuplicateKeyError

from app.core.db import Db
from app.core.documents import Document, now
from app.modules.artifacts.models import ValidationResult
from app.modules.projects.models import Project


class Plan(Document):
    project_id: uuid.UUID
    owner_id: uuid.UUID
    version: int
    content: dict[str, Any]
    validation: ValidationResult = ValidationResult()
    fallback: bool = False
    run_id: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=now)

    def to_doc(self) -> dict[str, Any]:
        doc = super().to_doc()
        doc["validation"] = {"issues": [i.model_dump() for i in self.validation.issues]}
        return doc


async def save_plan(
    db: Db,
    project: Project,
    content: dict[str, Any],
    validation: ValidationResult,
    fallback: bool,
    run_id: uuid.UUID | None,
) -> Plan:
    for _ in range(3):
        last = await db.plans.find_one(
            {"project_id": project.id}, sort=[("version", -1)], projection={"version": 1}
        )
        plan = Plan(
            project_id=project.id,
            owner_id=project.owner_id,
            version=(last["version"] if last else 0) + 1,
            content=content,
            validation=validation,
            fallback=fallback,
            run_id=run_id,
        )
        try:
            await db.plans.insert_one(plan.to_doc())
            return plan
        except DuplicateKeyError:
            continue
    raise RuntimeError("Could not allocate a plan version")


async def current_plan(db: Db, project: Project) -> Plan | None:
    doc = await db.plans.find_one({"project_id": project.id}, sort=[("version", -1)])
    return Plan.from_doc(doc) if doc else None


async def get_plan(db: Db, project: Project, version: int) -> Plan | None:
    doc = await db.plans.find_one({"project_id": project.id, "version": version})
    return Plan.from_doc(doc) if doc else None


async def list_plans(db: Db, project: Project, limit: int = 50) -> list[Plan]:
    cursor = db.plans.find({"project_id": project.id}).sort("version", -1).limit(limit)
    return [Plan.from_doc(d) async for d in cursor]


def merge_brief(brief: dict[str, Any], preferences: dict[str, Any] | None) -> dict[str, Any]:
    """User preferences override the orchestrator's constraints, field by field."""
    merged = dict(brief)
    prefs = {k: v for k, v in (preferences or {}).items() if v not in (None, "", [])}
    if prefs:
        merged["constraints"] = {**(brief.get("constraints") or {}), **prefs}
    return merged
