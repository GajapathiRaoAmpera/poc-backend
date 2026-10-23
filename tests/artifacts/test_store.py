import pathlib
import re

import pytest

from app.core.errors import AppError
from app.modules.artifacts import service
from app.modules.artifacts.models import SourceRef
from tests.artifacts.conftest import new_run_id


async def _add(db, project, owner, kind="parent", base=None, **content):
    return await service.add_revision(
        db,
        project,
        kind,
        {"title": "t", **content},
        sources=[],
        origin="user",
        summary="s",
        created_by=owner.id,
        base_revision=base,
    )


async def test_revisions_number_sequentially(db, project, owner):
    r1 = await _add(db, project, owner)
    r2 = await _add(db, project, owner, base=1)
    assert (r1.revision, r2.revision) == (1, 2)
    art = await service.get_artifact(db, project, "parent")
    assert art.latest_revision == 2 and art.approved_revision is None
    assert [r.revision for r in await service.list_revisions(db, project, "parent")] == [2, 1]


def test_revision_immutable_no_update_path():
    """No code in app/ may update or replace an artifact revision document."""
    offenders = []
    for path in pathlib.Path("app").rglob("*.py"):
        text = path.read_text()
        if re.search(
            r"artifact_revisions\.(update_one|update_many|replace_one|find_one_and_update"
            r"|find_one_and_replace|bulk_write)",
            text,
        ):
            offenders.append(str(path))
    assert offenders == []


async def test_base_conflict(db, project, owner):
    await _add(db, project, owner)
    await _add(db, project, owner, base=1)
    with pytest.raises(AppError) as e:
        await _add(db, project, owner, base=1)
    assert e.value.code == "REVISION_CONFLICT" and e.value.status == 409
    assert e.value.details == {"latest_revision": 2}


async def test_duplicate_run_id_returns_existing(db, project, owner):
    rid = new_run_id()
    a = await service.add_revision(
        db,
        project,
        "parent",
        {"title": "t"},
        sources=[],
        origin="agent",
        summary="s",
        created_by=None,
        run_id=rid,
    )
    b = await service.add_revision(
        db,
        project,
        "parent",
        {"title": "other"},
        sources=[],
        origin="agent",
        summary="s",
        created_by=None,
        run_id=rid,
    )
    assert a.id == b.id and b.revision == 1
    assert (await service.get_artifact(db, project, "parent")).latest_revision == 1


async def test_save_blocking_validation_is_422(db, project, owner):
    with pytest.raises(AppError) as e:
        await _add(db, project, owner, broken=True)
    assert e.value.status == 422 and e.value.details[0]["code"] == "BROKEN"
    assert await service.get_artifact(db, project, "parent") is None or (
        (await service.get_artifact(db, project, "parent")).latest_revision == 0
    )


async def test_schema_violation_is_422(db, project, owner):
    with pytest.raises(AppError) as e:
        await service.add_revision(
            db,
            project,
            "parent",
            {"nope": 1},
            sources=[],
            origin="user",
            summary="s",
            created_by=owner.id,
        )
    assert e.value.status == 422


async def test_warnings_and_approval_errors_are_stored(db, project, owner):
    r = await _add(db, project, owner, warn=True, unapprovable=True)
    codes = {(i.severity, i.code) for i in r.validation.issues}
    assert codes == {("warning", "WARN"), ("error", "NOPE")}
    assert r.validation.ok is False


async def test_restore_creates_new_revision(db, project, owner):
    await _add(db, project, owner)
    await service.add_revision(
        db,
        project,
        "parent",
        {"title": "second"},
        sources=[],
        origin="user",
        summary="s",
        created_by=owner.id,
        base_revision=1,
    )
    r3 = await service.restore(db, project, "parent", 1, owner)
    assert r3.revision == 3 and r3.origin == "restore" and r3.restored_from == 1
    assert r3.content == {"title": "t", "broken": False, "unapprovable": False, "warn": False}


async def test_get_unknown_revision_404(db, project, owner):
    await _add(db, project, owner)
    with pytest.raises(AppError) as e:
        await service.get_revision(db, project, "parent", 9)
    assert e.value.status == 404


async def test_sources_are_recorded(db, project, owner):
    src = [SourceRef(kind="specification", artifact_id=None, revision=3)]
    r = await service.add_revision(
        db,
        project,
        "parent",
        {"title": "t"},
        sources=src,
        origin="user",
        summary="s",
        created_by=owner.id,
    )
    assert r.sources == src
