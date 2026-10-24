import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.llm.fixtures import default_output
from app.llm.mock import MockProvider
from app.modules.delivery.graph import build_graph
from app.modules.delivery.state import GraphDeps


def scripted(**by_schema):
    """Mock script: per-schema override (dict, callable or exception), else the default fixture."""

    def script(schema_name, system, user):
        out = by_schema.get(schema_name)
        if out is None:
            return default_output(schema_name, system, user)
        if callable(out):
            return out(system, user)
        return out

    return script


class Harness:
    def __init__(self, db, project, script=None):
        self.db, self.project = db, project
        self.provider = MockProvider(script=script or scripted())
        self.graph = build_graph(InMemorySaver())
        self.cfg = {"configurable": {"thread_id": str(project.id)}}

    def deps(self):
        return GraphDeps(db=self.db, provider_factory=lambda: self.provider)

    async def start(self, trigger="intake"):
        await self.graph.ainvoke({"trigger": trigger}, self.cfg, context=self.deps())
        return await self.pending()

    async def resume(self, trigger, message=None):
        value = {"trigger": trigger, "message": message}
        await self.graph.ainvoke(Command(resume=value), self.cfg, context=self.deps())
        return await self.pending()

    async def pending(self):
        state = await self.graph.aget_state(self.cfg)
        for task in state.tasks:
            if task.interrupts:
                return task.interrupts[0].value
        return None

    async def runs(self, stage=None):
        q = {"project_id": self.project.id}
        if stage:
            q["stage"] = stage
        return [d async for d in self.db.agent_runs.find(q).sort("created_at", 1)]

    def calls(self, schema_name):
        return [c for c in self.provider.calls if c["schema_name"] == schema_name]


@pytest.fixture
def harness(db):
    return lambda project, script=None: Harness(db, project, script)
