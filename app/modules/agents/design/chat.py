"""UI/UX Design Agent chat: reply, or change screens from an instruction."""

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.chat_common import CHAT_RULES, Direction, Reply, conversation_block
from app.modules.agents.design import agent, prompts
from app.modules.agents.design.schema import DesignAgentOutput
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.conversations.service import add_message, recent_messages

CHANNEL = "design"

SYSTEM = f"""You are the UI/UX Design Agent, chatting with the client about the screen mockups
of their product.
Choose one action:
- "revise": the client wants screens added, changed or removed (or a new look), or there are no
  screens yet. Put a precise direction for the change in "instruction".
- "none": a question or remark that needs only a reply.
{CHAT_RULES}"""


class DesignChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: Reply
    action: Literal["none", "revise"]
    instruction: Direction | None = None


async def run(ctx: AgentContext, ins: agent.Inputs) -> AgentOutcome:
    db, project = ctx.db, ctx.project
    base_ref = next((s for s in ctx.run.inputs if s.kind == "design"), None)
    current = shots = None
    if base_ref:
        base = await artifacts.get_revision(db, project, "design", base_ref.revision)
        current = DesignAgentOutput.model_validate(
            {k: base.content[k] for k in ("summary", "theme", "screens", "flows")}
        )
        shots = base.content.get("screenshots", [])
    history = await recent_messages(db, project, CHANNEL)
    await ctx.emit("step", f"Read the conversation ({len(history)} message(s))", None)
    shown = prompts.screen_list(current.screens) if current else "(no screens yet)"
    user = (
        f"{ins.digest()}\n\n<screens>\n{shown}\n</screens>\n\n{conversation_block(history)}\n\n"
        f"<instruction>\n{(ctx.run.instruction or '')[:4000]}\n</instruction>"
    )
    out, usage = await call_structured(
        ctx,
        schema_model=DesignChatOutput,
        schema_name="design_chat_output",
        system=SYSTEM,
        user=user,
        validate=lambda o: [],
    )
    refs: dict = {"run_id": str(ctx.run.id)}
    output: dict = {"action": out.action}
    if out.action == "revise":
        direction = out.instruction or ctx.run.instruction or ""
        if current is None:
            run = ctx.run.model_copy(update={"instruction": direction})
            made = await agent.generate(dataclasses.replace(ctx, run=run), ins)
            output |= made.output
            agent._add_usage(usage, made.usage)
        else:
            changed, changes, used = await agent.request_changes(ctx, ins, current, direction)
            agent._add_usage(usage, used)
            touched = {c.screen.id for c in changes.changes if c.op == "write" and c.screen}
            old = {s["screen_id"]: s for s in shots}
            kept = [
                agent.Shot(**old[s.id])
                for s in changed.screens
                if s.id not in touched and s.id in old
            ]
            fresh = await agent.render_shots(ctx, [s for s in changed.screens if s.id in touched])
            output |= await agent.save(
                ctx, ins, changed, sorted(kept + fresh, key=lambda x: x.screen_id), changes.summary
            )
        refs["artifact_revision"] = output["revision"]
    await add_message(db, project, CHANNEL, "agent", out.reply, agent=agent.NAME, refs=refs)
    return AgentOutcome(output=output, summary=out.reply[:300], usage=usage)
