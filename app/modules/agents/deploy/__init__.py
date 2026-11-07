from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.deploy import agent
from app.modules.agents.deploy.mock_fixture import deploy_message
from app.modules.agents.deploy.schema import DeployContent
from app.modules.artifacts import registry
from app.modules.orchestration import stages


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="deploy",
            schema_version="1.0",
            model=DeployContent,
            validate_save=lambda c, ctx: [],
            validate_approval=lambda c, ctx: [],
        )
    )
    register_agent(agent.NAME, agent.run)
    register_default("deploy_message", deploy_message)
    stages.INPUT_CHECKS["deploy"] = lambda c: False
