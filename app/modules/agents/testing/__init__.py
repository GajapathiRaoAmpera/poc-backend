from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.testing import agent
from app.modules.agents.testing.mock_fixture import test_plan, test_step, testing_chat_output
from app.modules.agents.testing.schema import TestReport
from app.modules.artifacts import registry
from app.modules.artifacts.models import ValidationIssue
from app.modules.orchestration import stages


def _approval_issues(c: TestReport, ctx) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            severity="error",
            code="MUST_FAILED",
            message=f"MUST requirement {rid} has a failing test case",
            path=rid,
        )
        for rid in c.must_failed
    ]


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="testing",
            schema_version="1.0",
            model=TestReport,
            validate_save=lambda c, ctx: [],
            validate_approval=_approval_issues,
        )
    )
    register_agent(agent.NAME, agent.run)
    register_default("test_plan", test_plan)
    register_default("test_step", test_step)
    register_default("testing_chat_output", testing_chat_output)
    stages.INPUT_CHECKS["testing"] = lambda c: False
