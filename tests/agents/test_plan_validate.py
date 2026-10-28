from app.modules.agents.plan.schema import PlanAgentOutput
from app.modules.agents.plan.validate import plan_issues

ROLES = ["backend_engineer", "qa_engineer"]


def _task(i, deps=(), reqs=("FR-001",), role="backend_engineer", comps=("n_api",), ms="M-01"):
    return {
        "id": i,
        "title": i,
        "milestone_id": ms,
        "role": role,
        "effort_days": 1,
        "depends_on": list(deps),
        "requirement_ids": list(reqs),
        "component_ids": list(comps),
    }


def _out(*tasks, milestones=("M-01",)):
    return PlanAgentOutput.model_validate(
        {
            "summary": "s",
            "milestones": [{"id": m, "title": m, "goal": "g"} for m in milestones],
            "tasks": list(tasks),
            "risks": [],
        }
    )


def _issues(out, must=frozenset(), reqs=frozenset({"FR-001"})):
    return plan_issues(out, set(reqs), set(must), {"n_api"}, ROLES)


def test_clean():
    assert _issues(_out(_task("T-001"))) == []


def test_duplicate_id():
    assert [i.code for i in _issues(_out(_task("T-001"), _task("T-001")))] == ["DUPLICATE_ID"]


def test_unknown_refs():
    out = _out(_task("T-001", deps=["T-9"], reqs=["FR-9"], comps=["n_x"], ms="M-09"))
    issues = _issues(out)
    assert [i.code for i in issues] == ["UNKNOWN_REF"] * 4


def test_cycle_message():
    out = _out(_task("T-001", deps=["T-002"]), _task("T-002", deps=["T-001"]))
    (i,) = _issues(out)
    assert (i.code, i.message) == ("CYCLE", "Dependency cycle: T-001 → T-002 → T-001")


def test_must_uncovered():
    (i,) = _issues(_out(_task("T-001")), must={"FR-001", "FR-003"}, reqs={"FR-001", "FR-003"})
    assert i.code == "MUST_UNCOVERED" and "FR-003" in i.message


def test_unknown_role():
    (i,) = _issues(_out(_task("T-001", role="wizard")))
    assert i.code == "UNKNOWN_ROLE"
    assert "Allowed roles: backend_engineer, qa_engineer" in i.message
