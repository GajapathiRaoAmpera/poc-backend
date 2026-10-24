"""Project Orchestrator Agent: turns the objective + current facts into a validated plan."""

from app.modules.agents.base import AgentContext, AgentOutcome, OutputInvalid
from app.modules.agents.orchestrator import prompts
from app.modules.agents.orchestrator.policy import check_plan, default_plan
from app.modules.agents.orchestrator.schema import OrchestratorOutput
from app.modules.agents.structured import call_structured
from app.modules.artifacts.models import ValidationIssue, ValidationResult
from app.modules.conversations.service import add_message
from app.modules.delivery.facts import Facts, digest
from app.modules.delivery.plans import Plan, merge_brief, save_plan
from app.modules.projects.service import add_spec_revision
from app.modules.projects.spec_schema import ProjectSpecification

NAME = "project_orchestrator"
STAGE = "orchestrator"


def _brief_from_intake(messages: list[str]) -> ProjectSpecification | None:
    if not messages:
        return None
    return ProjectSpecification.model_validate(
        {
            "basics": {"name": " ".join(messages[0].split()[:6])[:160], "description": messages[0]},
            "problem": {"business_problem": "\n".join(messages)[:5000]},
        }
    )


async def plan_turn(ctx: AgentContext, facts: Facts) -> tuple[Plan, AgentOutcome]:
    db, project = ctx.db, ctx.project
    spec = await db.project_specifications.find_one(
        {"project_id": project.id}, sort=[("revision", -1)]
    )
    current = spec["data"] if spec else None
    await ctx.emit(
        "step",
        f"Read delivery state ({len(facts.intake_messages)} intake message(s), "
        f"specification r{facts.spec_revision or 0})",
        None,
    )
    issues: list[ValidationIssue] = []
    try:
        out, usage = await call_structured(
            ctx,
            schema_model=OrchestratorOutput,
            schema_name="orchestrator_output",
            system=prompts.SYSTEM,
            user=prompts.user_prompt(
                digest(facts), facts.intake_messages, current, facts.preferences
            ),
            validate=lambda o: check_plan(o.content, facts),
            max_repairs=1,
        )
        content, summary, fallback = out.content, out.summary, False
    except OutputInvalid as exc:
        issues = [ValidationIssue.model_validate(i) for i in exc.issues]
        brief = (
            ProjectSpecification.model_validate(current)
            if current
            else _brief_from_intake(facts.intake_messages)
        )
        content, fallback = default_plan(facts, brief), True
        summary = "Default plan used — the Orchestrator's plan failed validation"
        usage = {"input_tokens": 0, "output_tokens": 0}
        await ctx.emit("validation", summary, {"issues": [i.model_dump() for i in issues[:10]]})

    brief = merge_brief(content.brief.model_dump(mode="json", exclude_none=True), facts.preferences)
    if brief != current:
        rev = await add_spec_revision(db, project, brief, created_by=None, origin="orchestrator")
        await ctx.emit(
            "step", f"Saved project brief as specification revision {rev.revision}", None
        )
    if project.name_source == "intake" and content.project_name:
        await db.projects.update_one(
            {"_id": project.id},
            {"$set": {"name": content.project_name, "name_source": "orchestrator"}},
        )
    plan = await save_plan(
        db,
        project,
        content.model_dump(mode="json"),
        ValidationResult(issues=issues),
        fallback,
        ctx.run.id,
    )
    included = [s.stage for s in content.stages if s.include]
    await ctx.emit(
        "step",
        f"Plan v{plan.version}: {' → '.join(included) or 'no stages'}"
        + (" (default plan)" if fallback else ""),
        {"plan_version": plan.version},
    )
    await add_message(
        db,
        project,
        "intake",
        "agent",
        _intake_reply(content, fallback),
        agent=NAME,
        refs={"run_id": str(ctx.run.id), "plan_version": plan.version},
    )
    return plan, AgentOutcome(output={"plan_version": plan.version}, summary=summary, usage=usage)


def _intake_reply(content, fallback: bool) -> str:
    lines = [content.decision_summary or "Here is the delivery plan."]
    if fallback:
        lines.append("(My own plan failed validation, so the default plan is in use.)")
    steps = [s.stage for s in content.stages if s.include]
    lines.append("Plan: " + " → ".join(steps) + ". Each stage needs your approval.")
    blocking = [q for q in content.missing_info if q.blocking]
    others = [q for q in content.missing_info if not q.blocking]
    if blocking:
        lines.append("Before I continue, please answer:")
        lines += [f"- {q.question}" for q in blocking]
    if others:
        lines.append("Helpful to know (optional):")
        lines += [f"- {q.question}" for q in others]
    return "\n".join(lines)
