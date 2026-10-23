"""Versioned artifacts: immutable revisions, approval gates and stale propagation."""

import uuid
from typing import Any

from pydantic import ValidationError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.db import Db, in_transaction
from app.core.documents import now
from app.core.errors import NotApprovable, NotFound, RevisionConflict, ValidationFailed
from app.modules.artifacts import registry
from app.modules.artifacts.models import (
    Approval,
    Artifact,
    Origin,
    Revision,
    SourceRef,
    ValidationIssue,
    ValidationResult,
)
from app.modules.auth.models import User
from app.modules.projects.models import Project

LABELS = {
    "specification": "Specification",
    "requirements": "Requirements",
    "architecture": "Architecture",
}


def _label(kind: str) -> str:
    return LABELS.get(kind, kind.capitalize())


async def get_artifact(db: Db, project: Project, kind: str, session=None) -> Artifact | None:
    doc = await db.artifacts.find_one({"project_id": project.id, "kind": kind}, session=session)
    return Artifact.from_doc(doc) if doc else None


async def _ensure_artifact(db: Db, project: Project, kind: str) -> Artifact:
    art = Artifact(project_id=project.id, owner_id=project.owner_id, kind=kind)
    doc = await db.artifacts.find_one_and_update(
        {"project_id": project.id, "kind": kind},
        {"$setOnInsert": art.to_doc()},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return Artifact.from_doc(doc)


async def _current_revision(db: Db, project: Project, ref_kind: str, session=None) -> int | None:
    """The revision downstream work *should* be built on: latest spec, or approved artifact."""
    if ref_kind == "specification":
        last = await db.project_specifications.find_one(
            {"project_id": project.id}, sort=[("revision", -1)], session=session
        )
        return last["revision"] if last else None
    art = await get_artifact(db, project, ref_kind, session)
    return art.approved_revision if art else None


async def _outdated_sources(db, project, sources: list[SourceRef], session=None) -> list[str]:
    reasons = []
    for ref in sources:
        current = await _current_revision(db, project, ref.kind, session)
        if current != ref.revision:
            now_txt = f"revision {current}" if current is not None else "no approved revision"
            reasons.append(
                f"{_label(ref.kind)} changed: built on revision {ref.revision}, "
                f"current is {now_txt}"
            )
    return reasons


def _parse(kind: str, content: dict[str, Any]):
    spec = registry.get(kind)
    try:
        return spec, spec.model.model_validate(content)
    except ValidationError as exc:
        details = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        raise ValidationFailed("The content doesn't match the expected format", details) from exc


async def validate_content(
    db: Db, project: Project, kind: str, content: dict[str, Any], sources: list[SourceRef]
) -> tuple[Any, ValidationResult, list[ValidationIssue]]:
    """Returns (model, stored result, save-blocking errors). Raises 422 on schema errors."""
    spec, model = _parse(kind, content)
    ctx = await spec.load_context(db, project, sources)
    save_issues = spec.validate_save(model, ctx)
    blocking = [i for i in save_issues if i.severity == "error"]
    approval_issues = spec.validate_approval(model, ctx)
    return model, ValidationResult(issues=save_issues + approval_issues), blocking


async def add_revision(
    db: Db,
    project: Project,
    kind: str,
    content: dict[str, Any],
    *,
    sources: list[SourceRef],
    origin: Origin,
    summary: str,
    created_by: uuid.UUID | None,
    run_id: uuid.UUID | None = None,
    change_set_id: uuid.UUID | None = None,
    restored_from: int | None = None,
    base_revision: int | None = None,
) -> Revision:
    if run_id is not None:
        existing = await db.artifact_revisions.find_one({"run_id": run_id})
        if existing:  # idempotent agent write (e.g. worker crashed after writing)
            return Revision.from_doc(existing)

    spec = registry.get(kind)
    model, validation, blocking = await validate_content(db, project, kind, content, sources)
    if blocking:
        raise ValidationFailed(blocking[0].message, [i.model_dump() for i in blocking])
    art = await _ensure_artifact(db, project, kind)
    stale_reasons = await _outdated_sources(db, project, sources)

    async def txn(session) -> Revision:
        current = await db.artifacts.find_one({"_id": art.id}, session=session)
        latest = current["latest_revision"]
        if base_revision is not None and base_revision != latest:
            raise RevisionConflict(latest)
        rev = Revision(
            artifact_id=art.id,
            project_id=project.id,
            owner_id=project.owner_id,
            kind=kind,
            revision=latest + 1,
            schema_version=spec.schema_version,
            content=model.model_dump(mode="json"),
            sources=sources,
            origin=origin,
            run_id=run_id,
            change_set_id=change_set_id,
            restored_from=restored_from,
            summary=summary[:300],
            validation=validation,
            created_by=created_by,
        )
        await db.artifact_revisions.insert_one(rev.to_doc(), session=session)
        await db.artifacts.update_one(
            {"_id": art.id, "latest_revision": latest},
            {
                "$set": {
                    "latest_revision": latest + 1,
                    "updated_at": now(),
                    "stale": bool(stale_reasons),
                    "stale_reason": "; ".join(stale_reasons) or None,
                }
            },
            session=session,
        )
        return rev

    try:
        return await in_transaction(db, txn)
    except DuplicateKeyError as exc:
        if run_id is not None:
            existing = await db.artifact_revisions.find_one({"run_id": run_id})
            if existing:
                return Revision.from_doc(existing)
        latest = (await get_artifact(db, project, kind)).latest_revision
        raise RevisionConflict(latest) from exc


async def get_revision(db: Db, project: Project, kind: str, n: int) -> Revision:
    doc = await db.artifact_revisions.find_one(
        {"project_id": project.id, "kind": kind, "revision": n}
    )
    if doc is None:
        raise NotFound(f"{_label(kind)} revision {n} not found")
    return Revision.from_doc(doc)


async def latest_revision(db: Db, project: Project, kind: str) -> Revision | None:
    doc = await db.artifact_revisions.find_one(
        {"project_id": project.id, "kind": kind}, sort=[("revision", -1)]
    )
    return Revision.from_doc(doc) if doc else None


async def list_revisions(db: Db, project: Project, kind: str, limit: int = 100) -> list[Revision]:
    cursor = (
        db.artifact_revisions.find({"project_id": project.id, "kind": kind}, {"content": 0})
        .sort("revision", -1)
        .limit(limit)
    )
    return [Revision.from_doc({**d, "content": {}}) async for d in cursor]


async def restore(db: Db, project: Project, kind: str, n: int, user: User) -> Revision:
    old = await get_revision(db, project, kind, n)
    art = await get_artifact(db, project, kind)
    return await add_revision(
        db,
        project,
        kind,
        old.content,
        sources=old.sources,
        origin="restore",
        summary=f"Restored revision {n}",
        created_by=user.id,
        restored_from=n,
        base_revision=art.latest_revision,
    )


async def mark_stale(db: Db, project: Project, kind: str, reason: str, session=None) -> None:
    await db.artifacts.update_one(
        {"project_id": project.id, "kind": kind},
        {"$set": {"stale": True, "stale_reason": reason, "updated_at": now()}},
        session=session,
    )


async def mark_dependents_stale(
    db: Db, project: Project, kind: str, new_revision: int, session=None
) -> None:
    for dep in registry.DEPENDENTS.get(kind, []):
        dep_art = await get_artifact(db, project, dep, session)
        if dep_art is None or dep_art.latest_revision == 0:
            continue
        dep_rev = await db.artifact_revisions.find_one(
            {"artifact_id": dep_art.id, "revision": dep_art.latest_revision}, session=session
        )
        used = [s["revision"] for s in dep_rev.get("sources", []) if s["kind"] == kind]
        if used and used[0] != new_revision:
            await mark_stale(
                db,
                project,
                dep,
                f"{_label(kind)} changed: approved revision {new_revision} replaces revision "
                f"{used[0]} used by this {_label(dep).lower()}",
                session,
            )


async def _approval_blockers(db, project, art: Artifact, rev: Revision) -> list[str]:
    reasons = []
    if rev.revision != art.latest_revision:
        reasons.append(f"Only the latest revision ({art.latest_revision}) can be approved")
    if art.stale:
        reasons.append(f"This {_label(art.kind).lower()} is stale: {art.stale_reason}")
    errors = [i for i in rev.validation.issues if i.severity == "error"]
    if errors:
        reasons.append(f"Fix validation errors first: {errors[0].message}")
    return reasons


async def approve(
    db: Db, project: Project, kind: str, n: int, user: User, comment: str | None
) -> Approval:
    rev = await get_revision(db, project, kind, n)

    async def txn(session) -> Approval:
        art = await get_artifact(db, project, kind, session)
        reasons = await _approval_blockers(db, project, art, rev)
        if reasons:
            raise NotApprovable(reasons)
        approval = Approval(
            project_id=project.id,
            artifact_id=art.id,
            kind=kind,
            revision=n,
            decision="APPROVED",
            comment=comment,
            decided_by=user.id,
        )
        await db.approvals.insert_one(approval.to_doc(), session=session)
        await db.artifacts.update_one(
            {"_id": art.id},
            {
                "$set": {
                    "approved_revision": n,
                    "stale": False,
                    "stale_reason": None,
                    "updated_at": now(),
                }
            },
            session=session,
        )
        await mark_dependents_stale(db, project, kind, n, session)
        return approval

    return await in_transaction(db, txn)


async def request_changes(
    db: Db, project: Project, kind: str, n: int, user: User, comment: str | None
) -> Approval:
    rev = await get_revision(db, project, kind, n)
    approval = Approval(
        project_id=project.id,
        artifact_id=rev.artifact_id,
        kind=kind,
        revision=n,
        decision="CHANGES_REQUESTED",
        comment=comment,
        decided_by=user.id,
    )
    await db.approvals.insert_one(approval.to_doc())
    return approval


async def latest_decision(db: Db, project: Project, kind: str) -> Approval | None:
    doc = await db.approvals.find_one(
        {"project_id": project.id, "kind": kind}, sort=[("decided_at", -1)]
    )
    return Approval.from_doc(doc) if doc else None


async def list_approvals(db: Db, project: Project) -> list[Approval]:
    cursor = db.approvals.find({"project_id": project.id}).sort("decided_at", -1).limit(200)
    return [Approval.from_doc(d) async for d in cursor]
