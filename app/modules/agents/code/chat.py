"""Development Agent chat: reply, or change scaffold files from an instruction."""

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.chat_common import CHAT_RULES, Direction, Reply, conversation_block
from app.modules.agents.code import agent, prompts
from app.modules.agents.code.schema import CodeAgentOutput
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.conversations.service import add_message, recent_messages

CHANNEL = "code"

SYSTEM = f"""You are the Development Agent, chatting with the client about their project scaffold
(a script that writes the starting files of the project).
Choose one action:
- "revise": the client wants files added, changed or removed, or there is no scaffold yet. Put a
  precise direction for the change in "instruction".
- "none": a question or remark that needs only a reply.
{CHAT_RULES}"""


class CodeChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: Reply
    action: Literal["none", "revise"]
    instruction: Direction | None = None


async def run(ctx: AgentContext, ins: agent.Inputs) -> AgentOutcome:
    db, project = ctx.db, ctx.project
    base_ref = next((s for s in ctx.run.inputs if s.kind == "code"), None)
    current = None
    if base_ref:
        base = await artifacts.get_revision(db, project, "code", base_ref.revision)
        keys = ("summary", "project_slug", "files", "next_steps")
        current = CodeAgentOutput.model_validate({k: base.content[k] for k in keys})
    history = await recent_messages(db, project, CHANNEL)
    await ctx.emit("step", f"Read the conversation ({len(history)} message(s))", None)
    files = prompts.file_list(current.files) if current else "(no scaffold yet)"
    user = (
        f"{ins.digest()}\n\n<files>\n{files}\n</files>\n\n{conversation_block(history)}\n\n"
        f"<instruction>\n{(ctx.run.instruction or '')[:4000]}\n</instruction>"
    )
    out, usage = await call_structured(
        ctx,
        schema_model=CodeChatOutput,
        schema_name="code_chat_output",
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
            changed, summary, used = await agent.request_changes(
                ctx, current, prompts.chat_changes_prompt(ins.digest(), current.files, direction)
            )
            agent._add_usage(usage, used)
            changed = agent.with_plan(changed.model_copy(update={"summary": summary}), ins.plan)
            changed, report, used = await agent.validate_and_repair(ctx, changed, ins)
            agent._add_usage(usage, used)
            output |= await agent.save(ctx, ins, changed, report, summary)
        refs["artifact_revision"] = output["revision"]
    await add_message(db, project, CHANNEL, "agent", out.reply, agent=agent.NAME, refs=refs)
    return AgentOutcome(output=output, summary=out.reply[:300], usage=usage)
