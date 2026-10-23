import uuid

import pytest
from pydantic import BaseModel, ConfigDict

from app.modules.artifacts import registry
from app.modules.artifacts.models import ValidationIssue
from app.modules.projects.models import Project
from tests.factories import make_user


class Doc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    broken: bool = False  # save-blocking error
    unapprovable: bool = False  # approval-blocking error
    warn: bool = False


def _save_checks(c: Doc, ctx) -> list[ValidationIssue]:
    out = []
    if c.broken:
        out.append(
            ValidationIssue(severity="error", code="BROKEN", message="Broken", path="broken")
        )
    if c.warn:
        out.append(ValidationIssue(severity="warning", code="WARN", message="Careful", path=None))
    return out


def _approval_checks(c: Doc, ctx) -> list[ValidationIssue]:
    if c.unapprovable:
        return [ValidationIssue(severity="error", code="NOPE", message="Not yet", path=None)]
    return []


@pytest.fixture(autouse=True)
def test_kinds(monkeypatch):
    for kind in ("parent", "child"):
        registry.register(
            registry.KindSpec(
                kind=kind,
                schema_version="1.0",
                model=Doc,
                validate_save=_save_checks,
                validate_approval=_approval_checks,
            )
        )
    monkeypatch.setitem(registry.DEPENDENTS, "parent", ["child"])
    yield
    for kind in ("parent", "child"):
        registry.unregister(kind)


@pytest.fixture
async def owner(db):
    return await make_user(db, email="owner@x.com")


@pytest.fixture
async def project(db, owner):
    p = Project(owner_id=owner.id, name="P")
    await db.projects.insert_one(p.to_doc())
    return p


def new_run_id() -> uuid.UUID:
    return uuid.uuid4()
