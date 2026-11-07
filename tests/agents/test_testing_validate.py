from app.modules.agents.testing.schema import TestPlan
from app.modules.agents.testing.validate import coverage_issues

REQS = {
    "requirements": [
        {"id": "FR-001", "priority": "MUST"},
        {"id": "NFR-001", "priority": "MUST"},
        {"id": "FR-002", "priority": "SHOULD"},
    ]
}


def plan(*req_ids):
    return TestPlan(
        summary="s",
        cases=[
            {"id": f"TC-{i:03d}", "requirement_id": r, "title": "t", "kind": "ui", "steps": []}
            for i, r in enumerate(req_ids, 1)
        ],
    )


def test_plan_must_cover_all_must_criteria():
    issues = coverage_issues(plan("FR-001"), REQS)
    assert [i.code for i in issues] == ["MUST_UNCOVERED"] and issues[0].refs == ["NFR-001"]
    assert coverage_issues(plan("FR-001", "NFR-001"), REQS) == []


def test_unknown_requirement_is_an_issue():
    codes = [i.code for i in coverage_issues(plan("FR-001", "NFR-001", "FR-9"), REQS)]
    assert codes == ["REQ_UNKNOWN"]
