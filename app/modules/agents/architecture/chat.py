"""Architecture Agent chat: reply, propose a change set, or generate a new draft diagram."""

import dataclasses
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agents.architecture import agent
from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.chat_common import CHAT_RULES, Direction, Reply, conversation_block
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import ValidationIssue
from app.modules.changesets.service import create_change_set
from app.modules.conversations.service import add_message, recent_messages
from app.modules.diagrams.ops import Op, OpError, apply_ops
from app.modules.diagrams.schema import ArchitectureContent
from app.modules.diagrams.validate import validate_structure

CHANNEL = "architecture"

SYSTEM = f"""You are the Architecture Agent, chatting with the client about their system design.
Choose one action:
- "propose_changes": the client wants a change to <current_architecture>. Return "ops"
  (add_node, update_node, remove_node, add_edge, update_edge, remove_edge, add_group,
  add_decision) referencing existing IDs exactly; never touch what the request doesn't mention.
  The client reviews the change set before anything is applied.
- "generate": there is no architecture yet, or the client asks for a new design. Put the design
  direction in "instruction".
- "none": a question or remark that needs only a reply (e.g. explain a decision or trade-off).
{CHAT_RULES}"""


class ArchitectureChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: Reply
    action: Literal["none", "propose_changes", "generate"]
    ops: list[Op] = []
    instruction: Direction | None = None


def chat_prompt(reqs: dict, current: dict | None, history, instruction: str) -> str:
    return (
        f"<approved_requirements>\n{json.dumps(reqs, separators=(',', ':'))[:30000]}\n"
        f"</approved_requirements>\n\n<current_architecture>\n"
        f"{json.dumps(current) if current else ''}\n</current_architecture>\n\n"
        f"{conversation_block(history)}\n\n<instruction>\n{instruction[:4000]}\n</instruction>"
    )


async def run(ctx: AgentContext, req_ref, reqs: dict) -> AgentOutcome:
    db, project = ctx.db, ctx.project
    base_ref = next((s for s in ctx.run.inputs if s.kind == "architecture"), None)
    base = (
        await artifacts.get_revision(db, project, CHANNEL, base_ref.revision) if base_ref else None
    )
    base_content = ArchitectureContent.model_validate(base.content) if base else None
    history = await recent_messages(db, project, CHANNEL)
    await ctx.emit("step", f"Read the conversation ({len(history)} message(s))", None)

    def check(o: ArchitectureChatOutput) -> list[ValidationIssue]:
        if o.action != "propose_changes":
            return []
        if base_content is None:
            return [
                ValidationIssue(
                    severity="error",
                    code="NO_BASE",
                    message="There is no architecture yet: use generate",
                )
            ]
        if not o.ops:
            return [ValidationIssue(severity="error", code="NO_OPS", message="No ops given")]
        try:
            return validate_structure(apply_ops(base_content, o.ops))
        except OpError as exc:
            return [
                ValidationIssue(
                    severity="error",
                    code="INVALID_OP",
                    message=str(exc),
                    refs=[exc.ref] if exc.ref else [],
                )
            ]

    out, usage = await call_structured(
        ctx,
        schema_model=ArchitectureChatOutput,
        schema_name="architecture_chat_output",
        system=SYSTEM,
        user=chat_prompt(reqs, base.content if base else None, history, ctx.run.instruction or ""),
        validate=check,
    )
    refs: dict = {"run_id": str(ctx.run.id)}
    output: dict = {"action": out.action}
    if out.action == "propose_changes":
        cs = await create_change_set(
            db,
            project,
            run_id=ctx.run.id,
            base_revision=base_ref.revision,
            ops=[op.model_dump(mode="json") for op in out.ops],
            summary=out.reply[:500],
            instruction=ctx.run.instruction,
        )
        await ctx.emit(
            "step",
            f"Proposed {len(out.ops)} change(s) for review",
            {"change_set_id": str(cs.id), "status": cs.status},
        )
        refs["change_set_id"] = str(cs.id)
        output["change_set_id"] = str(cs.id)
    elif out.action == "generate":
        run = ctx.run.model_copy(update={"instruction": out.instruction or ctx.run.instruction})
        made = await agent.generate(dataclasses.replace(ctx, run=run), req_ref, reqs)
        refs["artifact_revision"] = made.output["revision"]
        output |= made.output
        for k in usage:
            usage[k] += made.usage.get(k, 0)
    await add_message(db, project, CHANNEL, "agent", out.reply, agent=agent.NAME, refs=refs)
    return AgentOutcome(output=output, summary=out.reply[:300], usage=usage)
