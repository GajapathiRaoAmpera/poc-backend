from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.budget import agent
from app.modules.agents.budget.mock_fixture import budget_chat_output, budget_output
from app.modules.agents.budget.schema import BudgetContent
from app.modules.artifacts import registry
from app.modules.orchestration import stages


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="budget",
            schema_version="1.0",
            model=BudgetContent,
            validate_save=lambda c, ctx: [],
            validate_approval=lambda c, ctx: [],
            load_context=_no_context,
        )
    )
    register_agent(agent.NAME, agent.run)
    register_default("budget_output", budget_output)
    register_default("budget_chat_output", budget_chat_output)
    stages.INPUT_CHECKS["budget"] = lambda c: False


async def _no_context(db, project, sources) -> registry.ValidationContext:
    return registry.ValidationContext(sources={})
