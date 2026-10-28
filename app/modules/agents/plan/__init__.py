from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.plan import agent
from app.modules.agents.plan.mock_fixture import plan_chat_output, plan_output
from app.modules.agents.plan.schema import PlanContent
from app.modules.agents.plan.validate import plan_issues
from app.modules.artifacts import registry
from app.modules.orchestration import stages
from app.modules.ratecard.service import effective


async def load_context(db, project, sources) -> registry.ValidationContext:
    async def content(kind: str) -> dict:
        ref = next((s for s in sources if s.kind == kind), None)
        doc = ref and await db.artifact_revisions.find_one(
            {"project_id": project.id, "kind": kind, "revision": ref.revision}
        )
        return (doc or {}).get("content", {})

    reqs = (await content("requirements")).get("requirements", [])
    nodes = (await content("architecture")).get("nodes", [])
    return registry.ValidationContext(
        sources={
            "requirement_ids": {r["id"] for r in reqs},
            "must_ids": {r["id"] for r in reqs if r.get("priority") == "MUST"},
            "component_ids": {n["id"] for n in nodes},
            "roles": (await effective(db, project)).role_names(),
        }
    )


def _approval(c: PlanContent, ctx: registry.ValidationContext):
    s = ctx.sources
    return plan_issues(
        c, s["requirement_ids"], s["must_ids"], s["component_ids"], s["roles"]
    )  # fmt: skip


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="plan",
            schema_version="1.0",
            model=PlanContent,
            validate_save=lambda c, ctx: [],
            validate_approval=_approval,
            load_context=load_context,
        )
    )
    register_agent(agent.NAME, agent.run)
    register_default("plan_output", plan_output)
    register_default("plan_chat_output", plan_chat_output)
    stages.INPUT_CHECKS["plan"] = lambda c: False
