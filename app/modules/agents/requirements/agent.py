from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.requirements import prompts
from app.modules.agents.requirements.schema import RequirementsAgentOutput
from app.modules.agents.requirements.validate import agent_issues
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts

NAME = "requirements_analyst"


async def _all_ids_ever(ctx: AgentContext) -> set[str]:
    ids: set[str] = set()
    async for d in ctx.db.artifact_revisions.find(
        {"project_id": ctx.project.id, "kind": "requirements"}, {"content.requirements.id": 1}
    ):
        ids.update(r["id"] for r in d.get("content", {}).get("requirements", []))
    return ids


async def run(ctx: AgentContext) -> AgentOutcome:
    spec_ref = next(s for s in ctx.run.inputs if s.kind == "specification")
    spec = await ctx.db.project_specifications.find_one(
        {"project_id": ctx.project.id, "revision": spec_ref.revision}
    )
    await ctx.emit("step", f"Loaded project specification revision {spec_ref.revision}", None)

    previous, retired, prev_q = None, set(), set()
    prev_ref = next((s for s in ctx.run.inputs if s.kind == "requirements"), None)
    if prev_ref is not None:
        rev = await artifacts.get_revision(ctx.db, ctx.project, "requirements", prev_ref.revision)
        previous = rev.content
        current_ids = {r["id"] for r in previous["requirements"]}
        retired = await _all_ids_ever(ctx) - current_ids
        prev_q = {q["id"] for q in previous.get("open_questions", [])}
        answered = sum(1 for q in previous.get("open_questions", []) if q.get("answer"))
        await ctx.emit(
            "step",
            f"Loaded requirements revision {prev_ref.revision} "
            f"({len(current_ids)} requirements, {answered} answered questions)",
            None,
        )

    out, usage = await call_structured(
        ctx,
        schema_model=RequirementsAgentOutput,
        schema_name="requirements_output",
        system=prompts.SYSTEM,
        user=prompts.user_prompt(spec["data"], previous, retired),
        validate=lambda o: agent_issues(o.content, retired, prev_q),
    )
    # Built from the spec only; the previous draft is context, not an approved source.
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "requirements",
        out.content.model_dump(mode="json"),
        sources=[spec_ref],
        origin="agent",
        summary=out.summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    c = out.content
    await ctx.emit(
        "step",
        f"Saved requirements revision {rev.revision} ({len(c.requirements)} "
        f"requirements, {len(c.open_questions)} open questions)",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return AgentOutcome(
        output={"artifact_id": str(rev.artifact_id), "revision": rev.revision},
        summary=out.summary,
        usage=usage,
    )
