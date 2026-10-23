import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.pagination import _PageParams
from app.modules.auth.models import User, UserRole
from app.modules.projects.models import Project, ProjectSpecification, ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectOut, ProjectUpdate

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
    db: AsyncSession, project_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ProjectSpecification]:
    if not project_ids:
        return {}
    rows = await db.execute(
        select(ProjectSpecification)
        .where(ProjectSpecification.project_id.in_(project_ids))
        .order_by(ProjectSpecification.project_id, ProjectSpecification.revision.desc())
        .ext(distinct_on(ProjectSpecification.project_id))
    )
    return {s.project_id: s for s in rows.scalars()}


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


async def get_project_for(db: AsyncSession, user: User, project_id: uuid.UUID) -> Project:
    """The single access gate for a project: owner or Super Admin, otherwise 404.

    404 (not 403) so other users can't learn which project IDs exist.
    """
    project = await db.get(Project, project_id)
    if project is None or (project.owner_id != user.id and user.role != UserRole.SUPER_ADMIN):
        raise NotFound("Project not found")
    return project


async def list_projects(
    db: AsyncSession,
    user: User,
    params: _PageParams,
    q: str | None,
    status: ProjectStatus | None,
) -> tuple[list[ProjectOut], int]:
    stmt = select(Project)
    if user.role != UserRole.SUPER_ADMIN:
        stmt = stmt.where(Project.owner_id == user.id)
    if q and q.strip():
        stmt = stmt.where(Project.name.ilike(f"%{q.strip()}%"))
    if status:
        stmt = stmt.where(Project.status == status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    projects = list(
        (
            await db.execute(
                stmt.order_by(Project.updated_at.desc(), Project.created_at.desc())
                .offset(params.offset)
                .limit(params.page_size)
            )
        ).scalars()
    )
    specs = await latest_specs(db, [p.id for p in projects])
    return [to_out(p, specs.get(p.id)) for p in projects], total


async def create_project(db: AsyncSession, user: User, data: ProjectCreate) -> ProjectOut:
    project = Project(owner_id=user.id, name=data.name, description=data.description)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return to_out(project, None)


async def get_project(db: AsyncSession, user: User, project_id: uuid.UUID) -> ProjectOut:
    project = await get_project_for(db, user, project_id)
    specs = await latest_specs(db, [project.id])
    return to_out(project, specs.get(project.id))


async def update_project(
    db: AsyncSession, user: User, project_id: uuid.UUID, data: ProjectUpdate
) -> ProjectOut:
    project = await get_project_for(db, user, project_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if key in ("name", "status") and value is None:
            continue
        setattr(project, key, value)
    project.updated_at = func.now()
    await db.commit()
    await db.refresh(project)
    specs = await latest_specs(db, [project.id])
    return to_out(project, specs.get(project.id))


async def delete_project(db: AsyncSession, user: User, project_id: uuid.UUID) -> None:
    project = await get_project_for(db, user, project_id)
    await db.delete(project)
    await db.commit()
