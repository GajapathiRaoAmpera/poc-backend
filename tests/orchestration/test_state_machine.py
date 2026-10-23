import asyncio

import pytest

from app.modules.orchestration import events, runs
from app.modules.orchestration.models import RunStatus
from app.modules.orchestration.service import start_stage
from tests.orchestration.conftest import add_spec

LEGAL = [
    ("QUEUED", "RUNNING"),
    ("QUEUED", "CANCELLED"),
    ("RUNNING", "SUCCEEDED"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "CANCELLED"),
    ("RUNNING", "QUEUED"),
]


@pytest.mark.parametrize(("a", "b"), LEGAL)
async def test_legal_transitions(db, project, owner, a, b):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    if a == "RUNNING":
        await runs.transition(db, run.id, {RunStatus.QUEUED}, RunStatus.RUNNING)
    updated = await runs.transition(db, run.id, {RunStatus(a)}, RunStatus(b))
    assert updated.status == RunStatus(b)
    assert updated.active is (b in ("QUEUED", "RUNNING"))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("QUEUED", "SUCCEEDED"),
        ("SUCCEEDED", "RUNNING"),
        ("FAILED", "QUEUED"),
        ("CANCELLED", "RUNNING"),
    ],
)
async def test_illegal_transitions_rejected(db, project, owner, a, b):
    with pytest.raises(runs.IllegalTransition):
        runs.check_transition(RunStatus(a), RunStatus(b))


async def test_transition_from_wrong_current_status_fails(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    with pytest.raises(runs.IllegalTransition):
        await runs.transition(db, run.id, {RunStatus.RUNNING}, RunStatus.SUCCEEDED)


async def test_event_seq_strictly_increasing_under_concurrency(db, project, owner):
    await add_spec(db, project)
    run, _ = await start_stage(db, project, "requirements", "generate", owner)
    seqs = await asyncio.gather(
        *[events.emit(db, project.id, run.id, "step", f"e{i}") for i in range(20)]
    )
    assert len(set(seqs)) == 20
    stored = [e.seq async for e in events.iter_events(db, project.id, after=0)]
    assert stored == sorted(stored) and len(stored) == 21  # + the "queued" event
