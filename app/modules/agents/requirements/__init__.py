from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.requirements import agent
from app.modules.agents.requirements.mock_fixture import (
    requirements_chat_output,
    requirements_output,
)
from app.modules.agents.requirements.schema import RequirementsContent
from app.modules.agents.requirements.validate import (
    approval_issues,
    has_unanswered_blocking,
    structure_issues,
)
from app.modules.artifacts import registry
from app.modules.orchestration import stages


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="requirements",
            schema_version="1.0",
            model=RequirementsContent,
            validate_save=structure_issues,
            validate_approval=approval_issues,
        )
    )
    stages.INPUT_CHECKS["requirements"] = has_unanswered_blocking
    register_agent(agent.NAME, agent.run)
    register_default("requirements_output", requirements_output)
    register_default("requirements_chat_output", requirements_chat_output)
