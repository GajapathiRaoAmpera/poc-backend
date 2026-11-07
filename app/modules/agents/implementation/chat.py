"""Implementation Agent chat: reply, or re-implement from an instruction."""

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.chat_common import CHAT_RULES, Direction, Reply, conversation_block
from app.modules.agents.code.schema import CodeFile
from app.modules.agents.implementation import agent
from app.modules.agents.structured import call_structured
from app.modules.conversations.service import add_message, recent_messages

CHANNEL = "implementation"

SYSTEM = f"""You are the Implementation Agent, chatting with the client about the working code
built from their scaffold.
Choose one action:
- "revise": the client wants code changed or fixed. Put a precise direction in "instruction".
- "none": a question or remark that needs only a reply.
{CHAT_RULES}"""


class ImplementationChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: Reply
    action: Literal["none", "revise"]
    instruction: Direction | None = None


async def run(ctx: AgentContext, ins: agent.Inputs) -> AgentOutcome:
    db, project = ctx.db, ctx.project
    history = await recent_messages(db, project, CHANNEL)
    await ctx.emit("step", f"Read the conversation ({len(history)} message(s))", None)
    user = (
        f"{ins.digest()}\n\n{conversation_block(history)}\n\n"
        f"<instruction>\n{(ctx.run.instruction or '')[:4000]}\n</instruction>"
    )
    out, usage = await call_structured(
        ctx,
        schema_model=ImplementationChatOutput,
        schema_name="implementation_chat_output",
        system=SYSTEM,
        user=user,
        validate=lambda o: [],
    )
    refs: dict = {"run_id": str(ctx.run.id)}
    output: dict = {"action": out.action}
    if out.action == "revise":
        direction = out.instruction or ctx.run.instruction or ""
        run = ctx.run.model_copy(update={"instruction": direction})
        base = ins.base and [CodeFile.model_validate(f) for f in ins.base["files"]]
        made = await agent.generate(dataclasses.replace(ctx, run=run), ins, base)
        output |= made.output
        agent.code_agent._add_usage(usage, made.usage)
        refs["artifact_revision"] = output["revision"]
    await add_message(db, project, CHANNEL, "agent", out.reply, agent=agent.NAME, refs=refs)
    return AgentOutcome(output=output, summary=out.reply[:300], usage=usage)
