import pytest
from pydantic import BaseModel, ConfigDict

from app.modules.artifacts import registry
from app.modules.artifacts.models import ValidationIssue
from app.modules.orchestration import stages
from app.modules.projects.models import Project, ProjectSpecification
from tests.factories import make_user


class StubContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = "x"
    needs_answer: bool = False
    invalid: bool = False


def _approval(c: StubContent, ctx):
    if c.invalid:
        return [ValidationIssue(severity="error", code="BAD", message="Bad content")]
    return []


@pytest.fixture(autouse=True)
def stub_kinds(monkeypatch):
    saved = {k: registry._KINDS.get(k) for k in ("requirements", "architecture")}
    for kind in saved:
        registry.register(
            registry.KindSpec(
                kind=kind,
                schema_version="1.0",
                model=StubContent,
                validate_save=lambda c, ctx: [],
                validate_approval=_approval,
            )
        )
    monkeypatch.setitem(
        stages.INPUT_CHECKS, "requirements", lambda content: content["needs_answer"]
    )
    yield
    for kind, spec in saved.items():
        if spec:
            registry.register(spec)
        else:
            registry.unregister(kind)


@pytest.fixture
async def owner(db):
    return await make_user(db, email="owner@x.com")


@pytest.fixture
async def project(db, owner):
    p = Project(owner_id=owner.id, name="P")
    await db.projects.insert_one(p.to_doc())
    return p


async def add_spec(db, project, revision=1):
    spec = ProjectSpecification(
        project_id=project.id, revision=revision, data={"basics": {"name": "P"}}
    )
    await db.project_specifications.insert_one(spec.to_doc())
