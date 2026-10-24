from app.llm.fixtures import register_default
from app.modules.agents.architecture import agent
from app.modules.agents.architecture.mock_fixture import (
    architecture_chat_output,
    architecture_output,
    change_set_output,
)
from app.modules.agents.base import register_agent


def register() -> None:
    register_agent(agent.NAME, agent.run)
    register_default("architecture_output", architecture_output)
    register_default("change_set_output", change_set_output)
    register_default("architecture_chat_output", architecture_chat_output)
