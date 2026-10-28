"""The per-project delivery graph: orchestrator ⇄ stage agents, with interrupt gates."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.modules.delivery.nodes import gate_node, orchestrator_node, stage_node
from app.modules.delivery.state import DeliveryState, GraphDeps

STAGE_NODES = ("requirements", "architecture", "plan", "budget")


def _next(state: DeliveryState) -> str:
    node = state["decision"]["node"]
    return END if node == "end" else node


def build_graph(checkpointer: BaseCheckpointSaver):
    g = StateGraph(DeliveryState, context_schema=GraphDeps)
    g.add_node("orchestrator", orchestrator_node)
    for stage in STAGE_NODES:
        g.add_node(stage, stage_node(stage))
        g.add_edge(stage, "orchestrator")
    g.add_node("gate", gate_node)
    g.add_edge(START, "orchestrator")
    g.add_conditional_edges("orchestrator", _next, [*STAGE_NODES, "gate", END])
    g.add_edge("gate", "orchestrator")
    return g.compile(checkpointer=checkpointer)
