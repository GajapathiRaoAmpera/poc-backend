import uuid

from pymongo.errors import DuplicateKeyError

from app.core.db import Db
from app.core.documents import now
from app.core.errors import Conflict, NotFound, RevisionConflict
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import Revision, ValidationIssue, ValidationResult
from app.modules.auth.models import User
from app.modules.changesets.models import ChangeSet
from app.modules.diagrams.diff import Impact, impact
from app.modules.diagrams.ops import OpError, apply_ops, parse_ops
from app.modules.diagrams.schema import ArchitectureContent
from app.modules.projects.models import Project


async def create_change_set(
    db: Db,
    project: Project,
    *,
    run_id: uuid.UUID | None,
    base_revision: int,
    ops: list[dict],
    summary: str,
    instruction: str | None,
) -> ChangeSet:
    if run_id is not None:
        existing = await db.change_sets.find_one({"run_id": run_id})
        if existing:  # idempotent agent write
            return ChangeSet.from_doc(existing)
    base = await artifacts.get_revision(db, project, "architecture", base_revision)
    base_content = ArchitectureContent.model_validate(base.content)
    status, issues, imp = (
        "PROPOSED",
        [],
        Impact(added=[], removed=[], modified=[], requirement_ids=[]),
    )
    try:
        result = apply_ops(base_content, parse_ops(ops))
        imp = impact(base_content, result)
        _, validation, blocking = await artifacts.validate_content(
            db, project, "architecture", result.model_dump(mode="json"), base.sources
        )
        issues = validation.issues
        if blocking:
            status = "INVALID"
    except OpError as exc:
        status = "INVALID"
        issues = [ValidationIssue(severity="error", code="INVALID_OP", message=str(exc))]
    cs = ChangeSet(
        project_id=project.id,
        owner_id=project.owner_id,
        artifact_id=base.artifact_id,
        base_revision=base_revision,
        run_id=run_id,
        instruction=instruction,
        status=status,
        ops=ops,
        summary=summary[:500],
        impact=imp,
        validation=ValidationResult(issues=issues),
    )
    try:
        await db.change_sets.insert_one(cs.to_doc())
    except DuplicateKeyError:
        return ChangeSet.from_doc(await db.change_sets.find_one({"run_id": run_id}))
    return cs


async def get_change_set(db: Db, project: Project, change_set_id) -> ChangeSet:
    doc = await db.change_sets.find_one(
        {"_id": uuid.UUID(str(change_set_id)), "project_id": project.id}
    )
    if doc is None:
        raise NotFound("Change set not found")
    return ChangeSet.from_doc(doc)


async def _decide(db: Db, cs: ChangeSet, status: str, user: User | None, **extra) -> None:
    await db.change_sets.update_one(
        {"_id": cs.id, "status": "PROPOSED"},
        {
            "$set": {
                "status": status,
                "decided_by": user.id if user else None,
                "decided_at": now(),
                **extra,
            }
        },
    )


async def apply_change_set(db: Db, project: Project, change_set_id, user: User) -> Revision:
    cs = await get_change_set(db, project, change_set_id)
    if cs.status != "PROPOSED":
        raise Conflict(f"This change set is {cs.status.lower()} and can't be applied")
    art = await artifacts.get_artifact(db, project, "architecture")
    if art.latest_revision != cs.base_revision:
        await _decide(db, cs, "SUPERSEDED", None)
        raise RevisionConflict(art.latest_revision)
    base = await artifacts.get_revision(db, project, "architecture", cs.base_revision)
    result = apply_ops(ArchitectureContent.model_validate(base.content), parse_ops(cs.ops))
    try:
        rev = await artifacts.add_revision(
            db,
            project,
            "architecture",
            result.model_dump(mode="json"),
            sources=base.sources,
            origin="change_set",
            summary=cs.summary or "Applied AI change set",
            created_by=user.id,
            change_set_id=cs.id,
            base_revision=cs.base_revision,
        )
    except RevisionConflict:
        await _decide(db, cs, "SUPERSEDED", None)
        raise
    await _decide(db, cs, "APPLIED", user, applied_revision=rev.revision)
    return rev


async def reject_change_set(db: Db, project: Project, change_set_id, user: User) -> ChangeSet:
    cs = await get_change_set(db, project, change_set_id)
    if cs.status != "PROPOSED":
        raise Conflict(f"This change set is {cs.status.lower()} and can't be rejected")
    await _decide(db, cs, "REJECTED", user)
    return await get_change_set(db, project, change_set_id)
