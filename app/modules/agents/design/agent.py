import base64
import logging
from dataclasses import dataclass

from app.modules import mcp
from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.design import prompts
from app.modules.agents.design.schema import (
    DesignAgentOutput,
    DesignChanges,
    DesignContent,
    Screen,
    Shot,
)
from app.modules.agents.design.validate import apply_changes, design_issues
from app.modules.agents.structured import call_structured
from app.modules.agents.subtask import subtask
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef, ValidationIssue
from app.modules.media import service as media

logger = logging.getLogger(__name__)

NAME = "ui_designer"
MAX_OUTPUT_TOKENS = 16000


@dataclass
class Inputs:
    req_ref: SourceRef
    arch_ref: SourceRef
    reqs: dict
    arch: dict

    @property
    def sources(self) -> list[SourceRef]:
        return [self.req_ref, self.arch_ref]

    def digest(self) -> str:
        return prompts.digest(self.reqs, self.arch)


async def load_inputs(ctx: AgentContext) -> Inputs:
    refs = {s.kind: s for s in ctx.run.inputs}
    db, project = ctx.db, ctx.project
    reqs = await artifacts.get_revision(db, project, "requirements", refs["requirements"].revision)
    arch = await artifacts.get_revision(db, project, "architecture", refs["architecture"].revision)
    return Inputs(refs["requirements"], refs["architecture"], reqs.content, arch.content)


def _add_usage(total: dict[str, int], more: dict[str, int]) -> None:
    for k, v in more.items():
        total[k] = total.get(k, 0) + v


async def render_shots(ctx: AgentContext, screens: list[Screen]) -> list[Shot]:
    """One navigate + one screenshot per screen in a single browser session. Never raises."""
    shots: list[Shot] = []
    s: Screen | None = None
    try:
        async with mcp.get_browser() as browser:
            for s in screens:
                await subtask(ctx, s.id, s.name, "running", f"Rendering {s.id}: {s.name}")
                b64 = base64.b64encode(s.html.encode()).decode()
                await browser.call("browser_navigate", {"url": f"data:text/html;base64,{b64}"})
                png = await browser.screenshot()
                shots.append(
                    Shot(
                        screen_id=s.id,
                        media_id=await media.put(ctx.db, ctx.project, png, "image/png"),
                    )
                )
                await subtask(ctx, s.id, s.name, "passed", f"Rendered {s.id}")
    except Exception as exc:
        if s is not None and len(shots) < len(screens):
            await subtask(ctx, s.id, s.name, "failed", f"Could not render {s.id}")
        logger.warning("Design screenshots failed: %s", exc)
        await ctx.emit(
            "validation",
            f"Warning: screenshots could not be rendered ({str(exc)[:200]}); "
            "the revision is saved without them",
            None,
        )
    return shots


async def save(
    ctx: AgentContext, ins: Inputs, out: DesignAgentOutput, shots: list[Shot], summary: str
):
    content = DesignContent(**out.model_dump(), screenshots=shots)
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "design",
        content.model_dump(mode="json"),
        sources=ins.sources,
        origin="agent",
        summary=summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    await ctx.emit(
        "step",
        f"Saved design revision {rev.revision} ({len(out.screens)} screens, "
        f"{len(shots)} screenshots)",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return {"artifact_id": str(rev.artifact_id), "revision": rev.revision}


def _changes_issues(out: DesignAgentOutput, changes: DesignChanges, reqs: dict):
    try:
        merged = apply_changes(out, changes.changes)
    except ValueError as exc:
        return [ValidationIssue(severity="error", code="CHANGE", message=str(exc))]
    return design_issues(merged, reqs)


async def request_changes(
    ctx: AgentContext, ins: Inputs, out: DesignAgentOutput, instruction: str
) -> tuple[DesignAgentOutput, DesignChanges, dict[str, int]]:
    changes, usage = await call_structured(
        ctx,
        schema_model=DesignChanges,
        schema_name="design_changes",
        system=prompts.CHANGES_SYSTEM,
        user=prompts.chat_changes_prompt(ins.digest(), out, instruction),
        validate=lambda c: _changes_issues(out, c, ins.reqs),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    return apply_changes(out, changes.changes), changes, usage


async def run(ctx: AgentContext) -> AgentOutcome:
    ins = await load_inputs(ctx)
    await ctx.emit(
        "step",
        f"Loaded approved requirements ({len(ins.reqs['requirements'])}) and architecture "
        f"({len(ins.arch['nodes'])} components)",
        None,
    )
    if ctx.run.mode == "chat":
        from app.modules.agents.design import chat

        return await chat.run(ctx, ins)
    return await generate(ctx, ins)


async def generate(ctx: AgentContext, ins: Inputs) -> AgentOutcome:
    out, usage = await call_structured(
        ctx,
        schema_model=DesignAgentOutput,
        schema_name="design_output",
        system=prompts.GENERATE_SYSTEM,
        user=prompts.generate_prompt(ins.digest(), ctx.run.instruction),
        validate=lambda o: design_issues(o, ins.reqs),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    shots = await render_shots(ctx, out.screens)
    output = await save(ctx, ins, out, shots, out.summary)
    return AgentOutcome(output=output, summary=out.summary, usage=usage)
