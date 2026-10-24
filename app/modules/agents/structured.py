"""One structured model call with schema + domain validation and a bounded repair loop."""

import json
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from app.modules.agents.base import AgentContext, OutputInvalid
from app.modules.artifacts.models import ValidationIssue

MAX_REPAIRS = 2


def _pydantic_issues(exc: ValidationError) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            severity="error",
            code="SCHEMA",
            message=f"{'.'.join(str(p) for p in e.get('loc', [])) or 'output'}: {e.get('msg', '')}",
        )
        for e in exc.errors()[:20]
    ]


MAX_PREVIOUS_CHARS = 30000
# Characters per token used to keep a repair prompt inside the provider's input limit.
CHARS_PER_TOKEN = 4.5
_REPAIR_OVERHEAD = 1500  # tags, instructions and the listed errors


def _previous_budget(ctx: AgentContext, system: str, schema: dict, user: str) -> int:
    limit = getattr(ctx.provider, "max_input_tokens", None)
    if not limit:
        return MAX_PREVIOUS_CHARS
    room = int(limit * CHARS_PER_TOKEN) - len(system) - len(json.dumps(schema)) - len(user)
    return max(0, min(MAX_PREVIOUS_CHARS, room - _REPAIR_OVERHEAD))


def _repair_prompt(
    user: str, previous: dict, issues: list[ValidationIssue], budget: int = MAX_PREVIOUS_CHARS
) -> str:
    listed = "\n".join(f"- {i.message}" for i in issues[:20])
    return (
        f"{user}\n\n<previous_output>\n{json.dumps(previous)[:budget]}\n</previous_output>\n"
        f"<validation_errors>\n{listed}\n</validation_errors>\n"
        "Your previous output failed validation. Return a corrected JSON object that fixes every "
        "listed error and keeps everything else unchanged."
    )


async def call_structured[T: BaseModel](
    ctx: AgentContext,
    *,
    schema_model: type[T],
    schema_name: str,
    system: str,
    user: str,
    validate: Callable[[T], list[ValidationIssue]],
    max_output_tokens: int = 8000,
    timeout_s: float = 120,
    max_repairs: int = MAX_REPAIRS,
) -> tuple[T, dict[str, int]]:
    schema = schema_model.model_json_schema()
    usage = {"input_tokens": 0, "output_tokens": 0}
    prompt = user
    issues: list[ValidationIssue] = []
    for attempt in range(max_repairs + 1):
        label = f" (repair {attempt}/{max_repairs})" if attempt else ""
        await ctx.emit("step", f"Calling {ctx.provider.name}/{ctx.provider.model}{label}", None)
        result = await ctx.provider.generate(
            system=system,
            user=prompt,
            schema=schema,
            schema_name=schema_name,
            max_output_tokens=max_output_tokens,
            timeout_s=timeout_s,
        )
        usage["input_tokens"] += result.input_tokens or 0
        usage["output_tokens"] += result.output_tokens or 0
        try:
            parsed = schema_model.model_validate(result.data)
            issues = [i for i in validate(parsed) if i.severity == "error"]
        except ValidationError as exc:
            parsed, issues = None, _pydantic_issues(exc)
        if not issues:
            return parsed, usage
        tail = (
            f"requesting repair ({attempt + 1}/{max_repairs})"
            if attempt < max_repairs
            else "no repairs left"
        )
        await ctx.emit(
            "validation",
            f"Output failed validation: {issues[0].message} — {tail}",
            {"issues": [i.model_dump() for i in issues[:10]]},
        )
        budget = _previous_budget(ctx, system, schema, user)
        prompt = _repair_prompt(user, result.data, issues, budget)
    raise OutputInvalid([i.model_dump() for i in issues])
