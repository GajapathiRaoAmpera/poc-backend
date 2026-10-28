import logging
from dataclasses import dataclass

from app.modules.agents.base import AgentContext, AgentOutcome, OutputInvalid
from app.modules.agents.code import check, prompts
from app.modules.agents.code.render import render, sha256
from app.modules.agents.code.schema import (
    CodeAgentOutput,
    CodeChanges,
    CodeContent,
    CodeFile,
    Report,
)
from app.modules.agents.code.validate import apply_changes, manifest_issues, normalise, plan_md
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef, ValidationIssue

logger = logging.getLogger(__name__)

NAME = "developer"
REPAIR_ROUNDS = 2
MAX_OUTPUT_TOKENS = 14000


@dataclass
class Inputs:
    arch_ref: SourceRef
    plan_ref: SourceRef
    arch: dict
    plan: dict
    prefs: dict

    @property
    def sources(self) -> list[SourceRef]:
        return [self.arch_ref, self.plan_ref]

    @property
    def source_revisions(self) -> dict[str, int]:
        return {s.kind: s.revision for s in self.sources}

    def digest(self) -> str:
        return prompts.digest(self.arch, self.plan, self.prefs)


async def load_inputs(ctx: AgentContext) -> Inputs:
    refs = {s.kind: s for s in ctx.run.inputs}
    db, project = ctx.db, ctx.project
    arch = await artifacts.get_revision(db, project, "architecture", refs["architecture"].revision)
    plan = await artifacts.get_revision(db, project, "plan", refs["plan"].revision)
    return Inputs(
        refs["architecture"], refs["plan"], arch.content, plan.content, project.preferences or {}
    )


def _add_usage(total: dict[str, int], more: dict[str, int]) -> None:
    for k, v in more.items():
        total[k] = total.get(k, 0) + v


def with_plan(out: CodeAgentOutput, plan: dict) -> CodeAgentOutput:
    files = [f for f in out.files if not f.generated] + [plan_md(plan)]
    return out.model_copy(update={"files": files})


def _changes_issues(
    files: list[CodeFile], changes: CodeChanges, allowed: set[str] | None
) -> list[ValidationIssue]:
    if allowed is not None and (extra := {c.path for c in changes.changes} - allowed):
        msg = f"Only change the failing files; not {sorted(extra)}"
        return [ValidationIssue(severity="error", code="SCOPE", message=msg)]
    try:
        merged = apply_changes(files, changes.changes)
    except ValueError as exc:
        return [ValidationIssue(severity="error", code="CHANGE", message=str(exc))]
    if not merged:
        return [ValidationIssue(severity="error", code="CHANGE", message="Keep at least one file")]
    probe = CodeAgentOutput(summary="", project_slug="probe", files=merged)
    return manifest_issues(probe, allow_generated=True)


async def request_changes(
    ctx: AgentContext, out: CodeAgentOutput, user: str, allowed: set[str] | None = None
) -> tuple[CodeAgentOutput, str, dict[str, int]]:
    changes, usage = await call_structured(
        ctx,
        schema_model=CodeChanges,
        schema_name="code_changes",
        system=prompts.CHANGES_SYSTEM,
        user=user,
        validate=lambda c: _changes_issues(out.files, c, allowed),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    files = apply_changes(out.files, changes.changes)
    return out.model_copy(update={"files": files}), changes.summary, usage


async def validate_and_repair(
    ctx: AgentContext, out: CodeAgentOutput, ins: Inputs
) -> tuple[CodeAgentOutput, Report, dict[str, int]]:
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    for round_ in range(REPAIR_ROUNDS + 1):
        report = await check.check(render(out, ins.source_revisions), out.files)
        own = [
            f
            for f in report.findings
            if f.file == check.SCRIPT and f.code != "VALIDATOR_UNAVAILABLE"
        ]
        if own:  # the renderer owns every line outside the heredocs: our bug, not the model's
            logger.error("Rendered scaffold failed its own checks: %s", own)
        failing: dict[str, tuple[str, list[str]]] = {}
        by_path = {f.path: f for f in out.files}
        for f in report.findings:
            if f.file in by_path and not by_path[f.file].generated:
                content = by_path[f.file].content
                failing.setdefault(f.file, (content, []))[1].append(
                    f"line {f.line}: {f.code or f.tool}: {f.message}"
                )
        if not report.findings:
            await ctx.emit("validation", "Validation passed (bash -n and shellcheck)", None)
            return out, report, usage
        if not failing or round_ == REPAIR_ROUNDS:
            break
        files = ", ".join(sorted(failing))
        await ctx.emit(
            "validation",
            f"Validation: {len(report.findings)} finding(s) in {files} — repairing "
            f"(round {round_ + 1} of {REPAIR_ROUNDS})",
            {"findings": [f.model_dump() for f in report.findings[:10]]},
        )
        try:
            out, _, used = await request_changes(
                ctx, out, prompts.repair_prompt(out.project_slug, failing), set(failing)
            )
        except OutputInvalid:
            break
        _add_usage(usage, used)
    await ctx.emit(
        "validation",
        f"Validation found {len(report.findings)} problem(s); approval is blocked until fixed",
        {"findings": [f.model_dump() for f in report.findings[:10]]},
    )
    return out, report, usage


async def save(ctx: AgentContext, ins: Inputs, out: CodeAgentOutput, report: Report, summary):
    script = render(out, ins.source_revisions)
    content = CodeContent(**out.model_dump(), report=report, script_sha256=sha256(script))
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "code",
        content.model_dump(mode="json"),
        sources=ins.sources,
        origin="agent",
        summary=summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    await ctx.emit(
        "step",
        f"Saved scaffold revision {rev.revision} ({len(out.files)} files, "
        f"{len(script.encode()) // 1024 + 1} KB script)",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return {"artifact_id": str(rev.artifact_id), "revision": rev.revision}


async def run(ctx: AgentContext) -> AgentOutcome:
    ins = await load_inputs(ctx)
    await ctx.emit(
        "step",
        f"Loaded approved architecture ({len(ins.arch['nodes'])} components) and plan "
        f"({len(ins.plan['tasks'])} tasks)",
        None,
    )
    if ctx.run.mode == "chat":
        from app.modules.agents.code import chat

        return await chat.run(ctx, ins)
    return await generate(ctx, ins)


async def generate(ctx: AgentContext, ins: Inputs) -> AgentOutcome:
    out, usage = await call_structured(
        ctx,
        schema_model=CodeAgentOutput,
        schema_name="code_output",
        system=prompts.GENERATE_SYSTEM,
        user=prompts.generate_prompt(ins.digest(), ctx.run.instruction),
        validate=lambda o: manifest_issues(normalise(o)),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    out = with_plan(normalise(out), ins.plan)
    out, report, used = await validate_and_repair(ctx, out, ins)
    _add_usage(usage, used)
    output = await save(ctx, ins, out, report, out.summary)
    return AgentOutcome(output=output, summary=out.summary, usage=usage)
