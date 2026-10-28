import dataclasses
from datetime import date
from typing import Any

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.plan import prompts
from app.modules.agents.plan.schedule import compute_schedule
from app.modules.agents.plan.schema import PlanAgentOutput, PlanContent
from app.modules.agents.plan.validate import plan_issues
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.ratecard.service import effective

NAME = "project_planner"


@dataclasses.dataclass
class Inputs:
    req_ref: SourceRef
    arch_ref: SourceRef
    reqs: dict
    arch: dict
    roles: list[str]
    prefs: dict

    @property
    def schedule_args(self) -> tuple[date, dict[str, int], date | None]:
        p = self.prefs
        iso = lambda k: date.fromisoformat(p[k]) if p.get(k) else None  # noqa: E731
        return (
            iso("start_date") or date.today(),
            p.get("team_size") or {},
            iso("target_delivery_date"),
        )


async def load_inputs(ctx: AgentContext) -> Inputs:
    refs = {s.kind: s for s in ctx.run.inputs}
    db, project = ctx.db, ctx.project
    reqs = await artifacts.get_revision(db, project, "requirements", refs["requirements"].revision)
    arch = await artifacts.get_revision(db, project, "architecture", refs["architecture"].revision)
    card = await effective(db, project)
    return Inputs(
        refs["requirements"], refs["architecture"], reqs.content, arch.content,
        card.role_names(), project.preferences or {},
    )  # fmt: skip


async def _save(ctx, content: PlanContent, sources, origin, summary) -> AgentOutcome | dict:
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "plan",
        content.model_dump(mode="json"),
        sources=sources,
        origin=origin,
        summary=summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    await ctx.emit(
        "step",
        f"Saved plan revision {rev.revision} ({len(content.tasks)} tasks, "
        f"{content.schedule.working_days} working days)",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return {"artifact_id": str(rev.artifact_id), "revision": rev.revision}


async def run(ctx: AgentContext) -> AgentOutcome:
    ins = await load_inputs(ctx)
    await ctx.emit(
        "step",
        f"Loaded approved requirements and architecture ({len(ins.reqs['requirements'])} "
        f"requirements, {len(ins.arch['nodes'])} components)",
        None,
    )
    if ctx.run.mode == "reschedule":
        return await reschedule(ctx, ins)
    if ctx.run.mode == "chat":
        from app.modules.agents.plan import chat

        return await chat.run(ctx, ins)
    return await generate(ctx, ins)


async def generate(ctx: AgentContext, ins: Inputs) -> AgentOutcome:
    rs = ins.reqs["requirements"]
    req_ids = {r["id"] for r in rs}
    must = {r["id"] for r in rs if r["priority"] == "MUST"}
    comps = {n["id"] for n in ins.arch["nodes"]}
    plan_ref = next((s for s in ctx.run.inputs if s.kind == "plan"), None)
    current = (
        (await artifacts.get_revision(ctx.db, ctx.project, "plan", plan_ref.revision)).content
        if plan_ref
        else None
    )
    out, usage = await call_structured(
        ctx,
        schema_model=PlanAgentOutput,
        schema_name="plan_output",
        system=prompts.GENERATE_SYSTEM,
        user=prompts.generate_prompt(
            prompts.digest(ins.reqs, ins.arch, ins.roles, ins.prefs), ctx.run.instruction, current
        ),
        validate=lambda o: plan_issues(o, req_ids, must, comps, ins.roles),
        max_output_tokens=12000,
    )
    if current:  # keep the client's timeline pins on tasks that survive the revision
        pins = {
            t["id"]: date.fromisoformat(t["not_before"])
            for t in current["tasks"]
            if t.get("not_before")
        }
        tasks = [t.model_copy(update={"not_before": pins.get(t.id)}) for t in out.tasks]
        out = out.model_copy(update={"tasks": tasks})
    content = compute_schedule(out, *ins.schedule_args)
    output = await _save(ctx, content, [ins.req_ref, ins.arch_ref], "agent", out.summary)
    return AgentOutcome(output=output, summary=out.summary, usage=usage)


async def reschedule(ctx: AgentContext, ins: Inputs) -> AgentOutcome:
    ref = next(s for s in ctx.run.inputs if s.kind == "plan")
    base = await artifacts.get_revision(ctx.db, ctx.project, "plan", ref.revision)
    old = PlanContent.model_validate(base.content)
    dates: Any = {"start", "end"}
    out = PlanAgentOutput(
        summary=old.summary,
        milestones=[m.model_dump(exclude=dates) for m in old.milestones],
        tasks=[t.model_dump(exclude=dates) for t in old.tasks],
        risks=old.risks,
    )
    issues = plan_issues(
        out,
        {r["id"] for r in ins.reqs["requirements"]},
        {r["id"] for r in ins.reqs["requirements"] if r["priority"] == "MUST"},
        {n["id"] for n in ins.arch["nodes"]},
        ins.roles,
    )
    if issues:  # stored plan no longer valid (e.g. a role was removed): regenerate to heal it
        msg = "; ".join(i.message for i in issues[:5])
        await ctx.emit("step", f"Stored plan is invalid ({msg}); regenerating", None)
        fix = f"Fix these problems in the current plan: {msg}"
        run = ctx.run.model_copy(update={"instruction": fix})
        return await generate(dataclasses.replace(ctx, run=run), ins)
    content = compute_schedule(out, *ins.schedule_args)
    summary = "Rescheduled with updated team or start date"
    output = await _save(ctx, content, base.sources, "system", summary)
    return AgentOutcome(
        output=output, summary=summary, usage={"input_tokens": 0, "output_tokens": 0}
    )
