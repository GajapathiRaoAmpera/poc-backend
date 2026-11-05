import json

import httpx

from app.core.config import Settings
from app.llm.groq import GroqProvider
from app.llm.mock import MockProvider
from app.llm.registry import effort_for, for_stage


def _settings(**kw) -> Settings:
    return Settings(mongodb_uri="mongodb://x", **kw)


def test_effort_map_and_chat_drop():
    s = _settings()
    assert effort_for("code", "generate", s) == "high"
    assert effort_for("code", "chat", s) == "medium"
    assert effort_for("requirements", "chat", s) == "low"
    assert effort_for("orchestrator", "chat", s) == "low"
    assert effort_for("unknown", "generate", s) is None


def test_env_override_merges_and_drop_can_be_disabled():
    s = _settings(groq_effort={"code": "low"}, groq_chat_effort_drop=False)
    assert effort_for("code", "generate", s) == "low"
    assert effort_for("architecture", "chat", s) == "high"


def test_for_stage_passes_through_providers_without_effort():
    p = MockProvider()
    assert for_stage(p, "code", "generate") is p


async def test_request_carries_reasoning_effort_and_run_model_label():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"x": 1}'}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    base = GroqProvider(api_key="k", model="openai/gpt-oss-120b", http=http)
    p = base.with_effort("high")
    assert p.model == "openai/gpt-oss-120b@high" and base.model == "openai/gpt-oss-120b"
    await p.generate(
        system="s",
        user="u",
        schema={"type": "object"},
        schema_name="t",
        max_output_tokens=10,
        timeout_s=5,
    )
    assert seen["reasoning_effort"] == "high" and seen["model"] == "openai/gpt-oss-120b"
