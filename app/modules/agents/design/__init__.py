from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.design import agent
from app.modules.agents.design.mock_fixture import design_changes, design_chat_output, design_output
from app.modules.agents.design.schema import DesignContent
from app.modules.agents.design.validate import design_issues
from app.modules.artifacts import registry
from app.modules.orchestration import stages


async def load_context(db, project, sources) -> registry.ValidationContext:
    ref = next((s for s in sources if s.kind == "requirements"), None)
    doc = ref and await db.artifact_revisions.find_one(
        {"project_id": project.id, "kind": "requirements", "revision": ref.revision}
    )
    return registry.ValidationContext(sources={"requirements": (doc or {}).get("content") or {}})


def _issues(c: DesignContent, ctx, uncovered: bool):
    reqs = ctx.sources.get("requirements") or {}
    return [i for i in design_issues(c, reqs) if (i.code == "MUST_UNCOVERED") == uncovered]


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="design",
            schema_version="1.0",
            model=DesignContent,
            validate_save=lambda c, ctx: _issues(c, ctx, False),
            validate_approval=lambda c, ctx: _issues(c, ctx, True),
            load_context=load_context,
        )
    )
    register_agent(agent.NAME, agent.run)
    register_default("design_output", design_output)
    register_default("design_changes", design_changes)
    register_default("design_chat_output", design_chat_output)
    stages.INPUT_CHECKS["design"] = lambda c: False
