"""Plan Agent chat: reply, or revise the plan with an instruction."""

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.chat_common import CHAT_RULES, Direction, Reply, conversation_block
from app.modules.agents.plan import agent, prompts
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.conversations.service import add_message, recent_messages

CHANNEL = "plan"

SYSTEM = f"""You are the Project Planner, chatting with the client about their delivery plan.
Choose one action:
- "revise": the client wants a change to <current_plan>, or there is no plan yet. Put a precise
  direction for the new plan in "instruction".
- "none": a question or remark that needs only a reply.
{CHAT_RULES}"""


class PlanChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: Reply
    action: Literal["none", "revise"]
    instruction: Direction | None = None


async def run(ctx: AgentContext, ins: agent.Inputs) -> AgentOutcome:
    db, project = ctx.db, ctx.project
    base_ref = next((s for s in ctx.run.inputs if s.kind == "plan"), None)
    base = (
        await artifacts.get_revision(db, project, "plan", base_ref.revision) if base_ref else None
    )
    history = await recent_messages(db, project, CHANNEL)
    await ctx.emit("step", f"Read the conversation ({len(history)} message(s))", None)
    user = (
        f"{prompts.digest(ins.reqs, ins.arch, ins.roles, ins.prefs)}\n\n<current_plan>\n"
        f"{prompts.compact_plan(base.content) if base else ''}\n</current_plan>\n\n"
        f"{conversation_block(history)}\n\n"
        f"<instruction>\n{(ctx.run.instruction or '')[:4000]}\n</instruction>"
    )
    out, usage = await call_structured(
        ctx,
        schema_model=PlanChatOutput,
        schema_name="plan_chat_output",
        system=SYSTEM,
        user=user,
        validate=lambda o: [],
    )
    refs: dict = {"run_id": str(ctx.run.id)}
    output: dict = {"action": out.action}
    if out.action == "revise":
        run = ctx.run.model_copy(update={"instruction": out.instruction or ctx.run.instruction})
        made = await agent.generate(dataclasses.replace(ctx, run=run), ins)
        refs["artifact_revision"] = made.output["revision"]
        output |= made.output
        for k in usage:
            usage[k] += made.usage.get(k, 0)
    await add_message(db, project, CHANNEL, "agent", out.reply, agent=agent.NAME, refs=refs)
    return AgentOutcome(output=output, summary=out.reply[:300], usage=usage)
