"""Requirements Agent chat: reply, record answers, or produce a revised draft."""

import dataclasses
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.chat_common import CHAT_RULES, Direction, Reply, conversation_block
from app.modules.agents.requirements import agent
from app.modules.agents.requirements.service import save_answers
from app.modules.agents.structured import call_structured
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import ValidationIssue
from app.modules.auth.models import User
from app.modules.conversations.service import add_message, recent_messages

CHANNEL = "requirements"

SYSTEM = f"""You are the Requirements Agent, chatting with the client about their project's
requirements. Choose one action:
- "answer_questions": the client's message answers one or more open questions in
  <current_requirements>. Put the answers in "answers" keyed by question ID (e.g. "Q-001").
- "revise": the client asks for a change to the requirements. Put a precise direction for the
  revision in "instruction".
- "none": a question or remark that needs only a reply.
{CHAT_RULES}"""


class RequirementsChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: Reply
    action: Literal["none", "answer_questions", "revise"]
    answers: dict[str, Direction] = {}
    instruction: Direction | None = None


def chat_prompt(current: dict | None, history, instruction: str) -> str:
    return (
        f"<current_requirements>\n{json.dumps(current or {}, separators=(',', ':'))[:30000]}\n"
        f"</current_requirements>\n\n{conversation_block(history)}\n\n"
        f"<instruction>\n{instruction[:4000]}\n</instruction>"
    )


async def run(ctx: AgentContext) -> AgentOutcome:
    db, project = ctx.db, ctx.project
    latest = await artifacts.latest_revision(db, project, CHANNEL)
    current = latest.content if latest else None
    question_ids = {q["id"] for q in (current or {}).get("open_questions", [])}
    history = await recent_messages(db, project, CHANNEL)
    await ctx.emit("step", f"Read the conversation ({len(history)} message(s))", None)

    def check(o: RequirementsChatOutput) -> list[ValidationIssue]:
        if o.action != "answer_questions":
            return []
        unknown = sorted(set(o.answers) - question_ids)
        if not o.answers or unknown:
            msg = f"Unknown question IDs {unknown}" if unknown else "No answers given"
            return [ValidationIssue(severity="error", code="ANSWERS", message=msg)]
        return []

    out, usage = await call_structured(
        ctx,
        schema_model=RequirementsChatOutput,
        schema_name="requirements_chat_output",
        system=SYSTEM,
        user=chat_prompt(current, history, ctx.run.instruction or ""),
        validate=check,
        max_output_tokens=3000,
    )
    refs: dict = {"run_id": str(ctx.run.id)}
    output: dict = {"action": out.action}
    if out.action == "answer_questions":
        owner = User.from_doc(await db.users.find_one({"_id": project.owner_id}))
        rev = await save_answers(db, project, owner, out.answers, base_revision=latest.revision)
        await ctx.emit("step", f"Recorded {len(out.answers)} answer(s) as revision {rev.revision}")
        refs["artifact_revision"] = rev.revision
        output |= {"artifact_id": str(rev.artifact_id), "revision": rev.revision}
    elif out.action == "revise":
        run = ctx.run.model_copy(update={"instruction": out.instruction or ctx.run.instruction})
        drafted = await agent.draft(dataclasses.replace(ctx, run=run))
        refs["artifact_revision"] = drafted.output["revision"]
        output |= drafted.output
        for k in usage:
            usage[k] += drafted.usage.get(k, 0)
    await add_message(db, project, CHANNEL, "agent", out.reply, agent=agent.NAME, refs=refs)
    return AgentOutcome(output=output, summary=out.reply[:300], usage=usage)
