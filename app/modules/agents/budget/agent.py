import dataclasses
from dataclasses import dataclass

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.budget import prompts
from app.modules.agents.budget.calc import compute_budget
from app.modules.agents.budget.schema import BudgetAgentOutput, BudgetContent
from app.modules.agents.budget.validate import budget_issues
from app.modules.agents.plan.schema import PlanContent
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.projects.spec_schema import Budget
from app.modules.ratecard.models import RateCard
from app.modules.ratecard.service import effective

NAME = "budget_estimator"
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0}


@dataclass
class Inputs:
    plan_ref: SourceRef
    arch_ref: SourceRef
    plan: dict
    arch: dict
    card: RateCard
    pref: Budget | None

    @property
    def component_ids(self) -> set[str]:
        return {n["id"] for n in self.arch["nodes"]}

    @property
    def risk_ids(self) -> set[str]:
        return {r["id"] for r in self.plan.get("risks", [])}


async def load_inputs(ctx: AgentContext) -> Inputs:
    refs = {s.kind: s for s in ctx.run.inputs}
    db, project = ctx.db, ctx.project
    plan = await artifacts.get_revision(db, project, "plan", refs["plan"].revision)
    arch = await artifacts.get_revision(db, project, "architecture", refs["architecture"].revision)
    raw = (project.preferences or {}).get("budget")
    return Inputs(
        refs["plan"], refs["architecture"], plan.content, arch.content,
        await effective(db, project), Budget.model_validate(raw) if raw else None,
    )  # fmt: skip


async def _save(ctx, content: BudgetContent, sources, origin, summary) -> dict:
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "budget",
        content.model_dump(mode="json"),
        sources=sources,
        origin=origin,
        summary=summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    await ctx.emit(
        "step",
        f"Saved budget revision {rev.revision} (total {content.total} {content.currency})",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return {"artifact_id": str(rev.artifact_id), "revision": rev.revision}


async def run(ctx: AgentContext) -> AgentOutcome:
    ins = await load_inputs(ctx)
    await ctx.emit(
        "step",
        f"Loaded approved plan ({len(ins.plan['tasks'])} tasks) and architecture "
        f"({len(ins.arch['nodes'])} components)",
        None,
    )
    if ctx.run.mode == "reprice":
        return await reprice(ctx, ins)
    if ctx.run.mode == "chat":
        from app.modules.agents.budget import chat

        return await chat.run(ctx, ins)
    return await generate(ctx, ins)


async def generate(ctx: AgentContext, ins: Inputs) -> AgentOutcome:
    ref = next((s for s in ctx.run.inputs if s.kind == "budget"), None)
    current = (
        (await artifacts.get_revision(ctx.db, ctx.project, "budget", ref.revision)).content
        if ref
        else None
    )
    out, usage = await call_structured(
        ctx,
        schema_model=BudgetAgentOutput,
        schema_name="budget_output",
        system=prompts.GENERATE_SYSTEM,
        user=prompts.generate_prompt(
            prompts.digest(ins.plan, ins.arch, ins.card, ins.pref), ctx.run.instruction, current
        ),
        validate=lambda o: budget_issues(o, ins.component_ids, ins.risk_ids),
        max_output_tokens=4000,
    )
    content = compute_budget(PlanContent.model_validate(ins.plan), ins.card, out, ins.pref)
    output = await _save(ctx, content, [ins.plan_ref, ins.arch_ref], "agent", out.summary)
    return AgentOutcome(output=output, summary=out.summary, usage=usage)


async def reprice(ctx: AgentContext, ins: Inputs) -> AgentOutcome:
    ref = next(s for s in ctx.run.inputs if s.kind == "budget")
    base = await artifacts.get_revision(ctx.db, ctx.project, "budget", ref.revision)
    used = next((s.revision for s in base.sources if s.kind == "plan"), None)
    if used != ins.plan_ref.revision:  # the plan itself changed: its items may no longer fit
        await ctx.emit("step", "The plan changed since this budget; regenerating", None)
        return await generate(ctx, ins)
    old = BudgetContent.model_validate(base.content)
    out = BudgetAgentOutput(
        summary=old.summary,
        items=[i.model_dump() | {"amount": float(i.amount)} for i in old.items],
        contingency_pct=float(old.contingency_pct),
        contingency_reason=old.contingency_reason,
    )
    issues = budget_issues(out, ins.component_ids, ins.risk_ids)
    if issues:  # stored items no longer valid (e.g. a component was removed): regenerate
        msg = "; ".join(i.message for i in issues[:5])
        await ctx.emit("step", f"Stored budget is invalid ({msg}); regenerating", None)
        run = ctx.run.model_copy(update={"instruction": f"Fix these problems: {msg}"})
        return await generate(dataclasses.replace(ctx, run=run), ins)
    content = compute_budget(PlanContent.model_validate(ins.plan), ins.card, out, ins.pref)
    summary = "Repriced with updated rates"
    output = await _save(ctx, content, [ins.plan_ref, ins.arch_ref], "system", summary)
    return AgentOutcome(output=output, summary=summary, usage=dict(ZERO_USAGE))
