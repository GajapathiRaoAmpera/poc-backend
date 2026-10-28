from datetime import date

import pytest
from pydantic import ValidationError

from app.modules.agents.plan.schedule import compute_schedule
from app.modules.agents.plan.schema import PlanAgentOutput, PlanContent

MON = date(2026, 11, 2)


def _plan(*specs, milestones=("M-01",)):
    tasks = [
        {
            "id": i,
            "title": i,
            "milestone_id": ms,
            "role": role,
            "effort_days": eff,
            "depends_on": deps,
            "requirement_ids": [],
            "component_ids": [],
        }
        for i, eff, role, deps, ms in specs
    ]
    return PlanAgentOutput.model_validate(
        {
            "summary": "s",
            "milestones": [{"id": m, "title": m, "goal": "g"} for m in milestones],
            "tasks": tasks,
            "risks": [],
        }
    )


def _dates(c):
    return {t.id: (t.start, t.end) for t in c.tasks}


def test_dependency_order():
    c = compute_schedule(
        _plan(("T-001", 2, "a", [], "M-01"), ("T-002", 2, "b", ["T-001"], "M-01")), MON, {}, None
    )
    assert _dates(c) == {
        "T-001": (date(2026, 11, 2), date(2026, 11, 3)),
        "T-002": (date(2026, 11, 4), date(2026, 11, 5)),
    }


@pytest.mark.parametrize("n,second", [(1, date(2026, 11, 3)), (2, MON)])
def test_role_slots(n, second):
    p = _plan(
        ("T-001", 1, "backend_engineer", [], "M-01"), ("T-002", 1, "backend_engineer", [], "M-01")
    )
    c = compute_schedule(p, MON, {"backend_engineer": n}, None)
    assert _dates(c)["T-002"][0] == second


def test_weekend_start():
    c = compute_schedule(_plan(("T-001", 1, "a", [], "M-01")), date(2026, 11, 7), {}, None)
    assert c.tasks[0].start == date(2026, 11, 9)


def test_fractional_effort_ceils():
    c = compute_schedule(_plan(("T-001", 0.5, "a", [], "M-01")), MON, {}, None)
    assert c.tasks[0].end == MON


def test_spans_weekend():
    c = compute_schedule(_plan(("T-001", 3, "a", [], "M-01")), date(2026, 11, 5), {}, None)
    assert c.tasks[0].end == date(2026, 11, 9) and c.schedule.working_days == 3


def test_target_warning():
    p = _plan(("T-001", 10, "a", [], "M-01"))
    c = compute_schedule(p, MON, {}, date(2026, 11, 10))
    assert c.schedule.end == date(2026, 11, 13)
    assert c.warnings == ["Ends 3 working days after your target date (2026-11-10)"]
    assert compute_schedule(p, MON, {}, date(2026, 11, 13)).warnings == []


def test_milestone_dates_are_min_max_of_tasks():
    p = _plan(
        ("T-001", 1, "a", [], "M-01"),
        ("T-002", 2, "a", ["T-001"], "M-01"),
        ("T-003", 1, "b", [], "M-02"),
        milestones=("M-01", "M-02"),
    )
    m = {x.id: (x.start, x.end) for x in compute_schedule(p, MON, {}, None).milestones}
    assert m["M-01"] == (MON, date(2026, 11, 4)) and m["M-02"] == (MON, MON)


def test_stable_tie_order():
    p = _plan(("T-002", 1, "a", [], "M-01"), ("T-001", 1, "a", [], "M-01"))
    d = _dates(compute_schedule(p, MON, {"a": 1}, None))
    assert d["T-001"][0] == MON and d["T-002"][0] == date(2026, 11, 3)


def test_storage_round_trip():
    c = compute_schedule(_plan(("T-001", 1.5, "a", [], "M-01")), MON, {}, None)
    assert PlanContent.model_validate(c.model_dump(mode="json")) == c


def test_effort_validation():
    with pytest.raises(ValidationError):
        _plan(("T-001", 1.3, "a", [], "M-01"))
    with pytest.raises(ValidationError):
        _plan(("T-001", 20.5, "a", [], "M-01"))
