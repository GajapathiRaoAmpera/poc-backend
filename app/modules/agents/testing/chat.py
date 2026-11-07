"""Testing Agent chat: reply, or run the tests again."""

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.chat_common import CHAT_RULES, Reply, conversation_block
from app.modules.agents.structured import call_structured
from app.modules.agents.testing import agent
from app.modules.conversations.service import add_message, recent_messages

CHANNEL = "testing"

SYSTEM = f"""You are the Testing Agent, chatting with the client about the test results of
their running app.
Choose one action:
- "retest": the client wants the tests run again.
- "none": a question or remark that needs only a reply.
{CHAT_RULES}"""


class TestingChatOutput(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")
    reply: Reply
    action: Literal["none", "retest"]


async def run(ctx: AgentContext, ins: agent.Inputs) -> AgentOutcome:
    history = await recent_messages(ctx.db, ctx.project, CHANNEL)
    user = (
        f"{conversation_block(history)}\n\n"
        f"<instruction>\n{(ctx.run.instruction or '')[:4000]}\n</instruction>"
    )
    out, usage = await call_structured(
        ctx,
        schema_model=TestingChatOutput,
        schema_name="testing_chat_output",
        system=SYSTEM,
        user=user,
        validate=lambda o: [],
    )
    refs: dict = {"run_id": str(ctx.run.id)}
    output: dict = {"action": out.action}
    if out.action == "retest":
        made = await agent.generate(dataclasses.replace(ctx), ins)
        output |= made.output
        for k, v in made.usage.items():
            usage[k] = usage.get(k, 0) + v
        refs["artifact_revision"] = output["revision"]
    await add_message(ctx.db, ctx.project, CHANNEL, "agent", out.reply, agent=agent.NAME, refs=refs)
    return AgentOutcome(output=output, summary=out.reply[:300], usage=usage)
