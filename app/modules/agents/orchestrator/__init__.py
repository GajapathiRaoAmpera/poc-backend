from app.llm.fixtures import register_default
from app.modules.agents.orchestrator.mock_fixture import orchestrator_output


def register() -> None:
    # The orchestrator runs as a delivery-graph node, not as a queued agent run.
    register_default("orchestrator_output", orchestrator_output)
