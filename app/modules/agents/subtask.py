from typing import Literal

from app.modules.agents.base import AgentContext


async def subtask(
    ctx: AgentContext,
    id: str,
    label: str,
    status: Literal["running", "passed", "failed"],
    message: str,
) -> None:
    """A step event the agent-graph view turns into a moon of this agent's planet."""
    await ctx.emit("step", message, {"subtask": {"id": id, "label": label, "status": status}})
