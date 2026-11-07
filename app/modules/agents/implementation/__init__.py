from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.implementation import agent
from app.modules.agents.implementation.mock_fixture import (
    implementation_chat_output,
    sandbox_spec,
)
from app.modules.agents.implementation.schema import ImplementationContent
from app.modules.artifacts import registry
from app.modules.artifacts.models import ValidationIssue
from app.modules.orchestration import stages


def _approval_issues(c: ImplementationContent, ctx) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            severity="error",
            code="SANDBOX_UNHEALTHY",
            message=f"Service {h.service} is not healthy: {h.log_excerpt[-300:]}",
            path=h.service,
        )
        for h in c.health
        if not h.healthy
    ]


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="implementation",
            schema_version="1.0",
            model=ImplementationContent,
            validate_save=lambda c, ctx: agent.save_issues(c),
            validate_approval=_approval_issues,
        )
    )
    register_agent(agent.NAME, agent.run)
    register_default("sandbox_spec", sandbox_spec)
    register_default("implementation_chat_output", implementation_chat_output)
    stages.INPUT_CHECKS["implementation"] = lambda c: False
