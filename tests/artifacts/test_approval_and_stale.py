import pytest

from app.core.errors import AppError
from app.modules.artifacts import service
from app.modules.artifacts.models import SourceRef


async def _parent(db, project, owner, title="p", base=None):
    return await service.add_revision(
        db,
        project,
        "parent",
        {"title": title},
        sources=[],
        origin="user",
        summary="s",
        created_by=owner.id,
        base_revision=base,
    )


async def _child_on(db, project, owner, parent_rev, base=None):
    art = await service.get_artifact(db, project, "parent")
    return await service.add_revision(
        db,
        project,
        "child",
        {"title": "c"},
        sources=[SourceRef(kind="parent", artifact_id=art.id, revision=parent_rev)],
        origin="agent",
        summary="s",
        created_by=None,
        base_revision=base,
    )


async def test_approve_latest(db, project, owner):
    await _parent(db, project, owner)
    a = await service.approve(db, project, "parent", 1, owner, "ok")
    assert a.decision == "APPROVED" and a.comment == "ok"
    assert (await service.get_artifact(db, project, "parent")).approved_revision == 1


@pytest.mark.parametrize("reason", ["not_latest", "validation", "stale"])
async def test_not_approvable(db, project, owner, reason):
    if reason == "not_latest":
        await _parent(db, project, owner)
        await _parent(db, project, owner, base=1)
        n = 1
    elif reason == "validation":
        await service.add_revision(
            db,
            project,
            "parent",
            {"title": "t", "unapprovable": True},
            sources=[],
            origin="user",
            summary="s",
            created_by=owner.id,
        )
        n = 1
    else:
        await _parent(db, project, owner)
        await service.mark_stale(db, project, "parent", "Upstream changed")
        n = 1
    with pytest.raises(AppError) as e:
        await service.approve(db, project, "parent", n, owner, None)
    assert e.value.code == "NOT_APPROVABLE" and e.value.status == 409
    assert (await service.get_artifact(db, project, "parent")).approved_revision is None


async def test_approving_new_parent_marks_child_stale(db, project, owner):
    await _parent(db, project, owner)
    await service.approve(db, project, "parent", 1, owner, None)
    await _child_on(db, project, owner, 1)
    await service.approve(db, project, "child", 1, owner, None)

    await _parent(db, project, owner, title="p2", base=1)
    await service.approve(db, project, "parent", 2, owner, None)

    child = await service.get_artifact(db, project, "child")
    assert child.stale is True
    assert "revision 2" in child.stale_reason and "revision 1" in child.stale_reason
    with pytest.raises(AppError):
        await service.approve(db, project, "child", 1, owner, None)


async def test_new_child_revision_on_current_parent_clears_stale(db, project, owner):
    await _parent(db, project, owner)
    await service.approve(db, project, "parent", 1, owner, None)
    await _child_on(db, project, owner, 1)
    await _parent(db, project, owner, title="p2", base=1)
    await service.approve(db, project, "parent", 2, owner, None)
    assert (await service.get_artifact(db, project, "child")).stale is True

    await _child_on(db, project, owner, 2, base=1)
    child = await service.get_artifact(db, project, "child")
    assert child.stale is False and child.stale_reason is None
    await service.approve(db, project, "child", 2, owner, None)


async def test_child_on_old_parent_is_born_stale(db, project, owner):
    await _parent(db, project, owner)
    await service.approve(db, project, "parent", 1, owner, None)
    await _parent(db, project, owner, title="p2", base=1)
    await service.approve(db, project, "parent", 2, owner, None)
    await _child_on(db, project, owner, 1)
    assert (await service.get_artifact(db, project, "child")).stale is True


async def test_request_changes(db, project, owner):
    await _parent(db, project, owner)
    a = await service.request_changes(db, project, "parent", 1, owner, "Add auth")
    assert a.decision == "CHANGES_REQUESTED"
    decisions = await service.list_approvals(db, project)
    assert [d.decision for d in decisions] == ["CHANGES_REQUESTED"]
