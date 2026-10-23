import re
import uuid
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.core.db import Db, in_transaction
from app.core.documents import now
from app.core.errors import Conflict, NotFound
from app.core.pagination import _PageParams
from app.modules.auth.models import User, UserRole
from app.modules.projects.models import Project, ProjectSpecification, ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectOut, ProjectUpdate, SpecOut
from app.modules.projects.spec_schema import ProjectSpecification as SpecData

SPEC_SECTIONS = ("basics", "problem", "requirements", "constraints")


def _has_content(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_content(v) for v in value.values())
    if isinstance(value, list | str):
        return len(value) > 0
    return value is not None


def completeness(spec: dict[str, Any] | None) -> float:
    """Fraction of wizard sections with any content (0.0–1.0)."""
    if not spec:
        return 0.0
    filled = sum(1 for s in SPEC_SECTIONS if _has_content(spec.get(s)))
    return filled / len(SPEC_SECTIONS)


async def latest_specs(
    db: Db, project_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ProjectSpecification]:
    if not project_ids:
        return {}
    pipeline = [
        {"$match": {"project_id": {"$in": project_ids}}},
        {"$sort": {"project_id": 1, "revision": -1}},
        {"$group": {"_id": "$project_id", "doc": {"$first": "$$ROOT"}}},
    ]
    out: dict[uuid.UUID, ProjectSpecification] = {}
    async for row in await db.project_specifications.aggregate(pipeline):
        spec = ProjectSpecification.from_doc(row["doc"])
        out[spec.project_id] = spec
    return out


def to_out(project: Project, spec: ProjectSpecification | None) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        owner_id=project.owner_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        completeness=completeness(spec.data if spec else None),
    )


async def get_project_for(db: Db, user: User, project_id: uuid.UUID) -> Project:
    """The single access gate for a project: owner or Super Admin, otherwise 404.

    404 (not 403) so other users can't learn which project IDs exist.
    """
    doc = await db.projects.find_one({"_id": project_id})
    if doc is None:
        raise NotFound("Project not found")
    project = Project.from_doc(doc)
    if project.owner_id != user.id and user.role != UserRole.SUPER_ADMIN:
        raise NotFound("Project not found")
    return project


async def list_projects(
    db: Db,
    user: User,
    params: _PageParams,
    q: str | None,
    status: ProjectStatus | None,
) -> tuple[list[ProjectOut], int]:
    query: dict[str, Any] = {}
    if user.role != UserRole.SUPER_ADMIN:
        query["owner_id"] = user.id
    if q and q.strip():
        query["name"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    if status:
        query["status"] = status.value
    total = await db.projects.count_documents(query)
    cursor = (
        db.projects.find(query)
        .sort([("updated_at", -1), ("created_at", -1)])
        .skip(params.offset)
        .limit(params.page_size)
    )
    projects = [Project.from_doc(d) async for d in cursor]
    specs = await latest_specs(db, [p.id for p in projects])
    return [to_out(p, specs.get(p.id)) for p in projects], total


async def create_project(db: Db, user: User, data: ProjectCreate) -> ProjectOut:
    project = Project(owner_id=user.id, name=data.name, description=data.description)
    await db.projects.insert_one(project.to_doc())
    return to_out(project, None)


async def get_project(db: Db, user: User, project_id: uuid.UUID) -> ProjectOut:
    project = await get_project_for(db, user, project_id)
    specs = await latest_specs(db, [project.id])
    return to_out(project, specs.get(project.id))


async def update_project(
    db: Db, user: User, project_id: uuid.UUID, data: ProjectUpdate
) -> ProjectOut:
    project = await get_project_for(db, user, project_id)
    changes: dict[str, Any] = {}
    for key, value in data.model_dump(exclude_unset=True).items():
        if key in ("name", "status") and value is None:
            continue
        setattr(project, key, value)
        changes[key] = value.value if isinstance(value, ProjectStatus) else value
    project.updated_at = now()
    await db.projects.update_one(
        {"_id": project.id}, {"$set": {**changes, "updated_at": project.updated_at}}
    )
    specs = await latest_specs(db, [project.id])
    return to_out(project, specs.get(project.id))


async def delete_project(db: Db, user: User, project_id: uuid.UUID) -> None:
    project = await get_project_for(db, user, project_id)

    async def txn(session) -> None:
        await db.project_specifications.delete_many({"project_id": project.id}, session=session)
        await db.projects.delete_one({"_id": project.id}, session=session)

    await in_transaction(db, txn)


def _spec_out(spec: ProjectSpecification | None) -> SpecOut:
    if spec is None:
        return SpecOut(revision=0, data={}, created_at=None, created_by=None)
    return SpecOut(
        revision=spec.revision,
        data=spec.data,
        created_at=spec.created_at,
        created_by=spec.created_by,
    )


async def get_specification(db: Db, user: User, project_id: uuid.UUID) -> SpecOut:
    project = await get_project_for(db, user, project_id)
    return _spec_out((await latest_specs(db, [project.id])).get(project.id))


async def save_specification(db: Db, user: User, project_id: uuid.UUID, data: SpecData) -> SpecOut:
    """Append revision n+1. Never updates an existing revision."""
    project = await get_project_for(db, user, project_id)
    payload = data.model_dump(mode="json", exclude_none=True)
    new_name = data.basics.name if data.basics and data.basics.name else None

    for attempt in range(2):
        last = await db.project_specifications.find_one(
            {"project_id": project.id}, sort=[("revision", -1)], projection={"revision": 1}
        )
        spec = ProjectSpecification(
            project_id=project.id,
            revision=(last["revision"] if last else 0) + 1,
            data=payload,
            created_by=user.id,
        )
        try:
            # The unique (project_id, revision) index arbitrates concurrent saves.
            await db.project_specifications.insert_one(spec.to_doc())
        except DuplicateKeyError as exc:
            if attempt == 1:
                raise Conflict("The specification was changed by another save. Reload.") from exc
            continue
        update: dict[str, Any] = {"updated_at": now()}
        if new_name:
            update["name"] = new_name
        await db.projects.update_one({"_id": project.id}, {"$set": update})
        return _spec_out(spec)
    raise AssertionError("unreachable")
