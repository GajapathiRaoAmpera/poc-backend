from app.modules.agents.architecture import prompts
from app.modules.agents.architecture.layout import layered_positions
from app.modules.agents.architecture.schema import ArchitectureAgentOutput, ChangeSetAgentOutput
from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import ValidationIssue
from app.modules.changesets.service import create_change_set
from app.modules.diagrams.ops import OpError, apply_ops
from app.modules.diagrams.schema import ArchitectureContent
from app.modules.diagrams.validate import validate_semantics, validate_structure

NAME = "solution_architect"


def _issues_for(content: ArchitectureContent, reqs: dict) -> list[ValidationIssue]:
    ids = {r["id"] for r in reqs["requirements"]}
    must = {r["id"] for r in reqs["requirements"] if r["priority"] == "MUST"}
    texts = [f"{r['title']} {r['description']}" for r in reqs["requirements"]]
    return validate_structure(content) + [
        i for i in validate_semantics(content, ids, must, texts) if i.severity == "error"
    ]


async def run(ctx: AgentContext) -> AgentOutcome:
    req_ref = next(s for s in ctx.run.inputs if s.kind == "requirements")
    req_rev = await artifacts.get_revision(ctx.db, ctx.project, "requirements", req_ref.revision)
    reqs = req_rev.content
    await ctx.emit(
        "step",
        f"Loaded approved requirements revision {req_ref.revision} "
        f"({len(reqs['requirements'])} requirements)",
        None,
    )
    if ctx.run.mode == "change_request":
        return await _change_request(ctx, reqs)
    if ctx.run.mode == "chat":
        from app.modules.agents.architecture import chat

        return await chat.run(ctx, req_ref, reqs)
    return await generate(ctx, req_ref, reqs)


async def generate(ctx: AgentContext, req_ref, reqs: dict) -> AgentOutcome:
    out, usage = await call_structured(
        ctx,
        schema_model=ArchitectureAgentOutput,
        schema_name="architecture_output",
        system=prompts.GENERATE_SYSTEM,
        user=prompts.generate_prompt(reqs, ctx.run.instruction),
        validate=lambda o: _issues_for(o.content, reqs),
        max_output_tokens=12000,
    )
    content = out.content.model_dump(mode="json")
    content["nodes"] = layered_positions(content["nodes"])
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "architecture",
        content,
        sources=[req_ref],
        origin="agent",
        summary=out.summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    await ctx.emit(
        "step",
        f"Saved architecture revision {rev.revision} ({len(content['nodes'])} "
        f"components, {len(content['edges'])} connections)",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return AgentOutcome(
        output={"artifact_id": str(rev.artifact_id), "revision": rev.revision},
        summary=out.summary,
        usage=usage,
    )


async def _change_request(ctx: AgentContext, reqs: dict) -> AgentOutcome:
    base_ref = next(s for s in ctx.run.inputs if s.kind == "architecture")
    base = await artifacts.get_revision(ctx.db, ctx.project, "architecture", base_ref.revision)
    base_content = ArchitectureContent.model_validate(base.content)
    await ctx.emit("step", f"Loaded architecture revision {base_ref.revision} as the base", None)

    def check(o: ChangeSetAgentOutput) -> list[ValidationIssue]:
        try:
            return validate_structure(apply_ops(base_content, o.ops))
        except OpError as exc:
            return [ValidationIssue(severity="error", code="INVALID_OP", message=str(exc))]

    out, usage = await call_structured(
        ctx,
        schema_model=ChangeSetAgentOutput,
        schema_name="change_set_output",
        system=prompts.CHANGE_SYSTEM,
        user=prompts.change_prompt(reqs, base.content, ctx.run.instruction or ""),
        validate=check,
    )
    cs = await create_change_set(
        ctx.db,
        ctx.project,
        run_id=ctx.run.id,
        base_revision=base_ref.revision,
        ops=[op.model_dump(mode="json") for op in out.ops],
        summary=out.summary,
        instruction=ctx.run.instruction,
    )
    await ctx.emit(
        "step",
        f"Proposed {len(out.ops)} change(s) for review: {out.summary}",
        {"change_set_id": str(cs.id), "status": cs.status},
    )
    return AgentOutcome(output={"change_set_id": str(cs.id)}, summary=out.summary, usage=usage)
