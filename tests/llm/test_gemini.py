import json

import httpx
import pytest

from app.llm.base import ProviderError
from app.llm.gemini import GEMINI_URL, GeminiProvider

SCHEMA = {
    "type": "object",
    "properties": {"item": {"$ref": "#/$defs/Item"}},
    "required": ["item"],
    "$defs": {"Item": {"type": "object", "properties": {"x": {"type": "integer"}}}},
}


def _provider(handler, model="gemini-2.5-flash") -> GeminiProvider:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiProvider(api_key="secret-key-123", model=model, http=http)


def _ok(text: str, finish="STOP", usage=None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "thinking…", "thought": True}, {"text": text}],
                    },
                    "finishReason": finish,
                }
            ],
            "usageMetadata": usage
            or {"promptTokenCount": 11, "candidatesTokenCount": 7, "thoughtsTokenCount": 3},
        },
    )


async def _call(p: GeminiProvider):
    return await p.generate(
        system="sys",
        user="usr",
        schema=SCHEMA,
        schema_name="thing",
        max_output_tokens=500,
        timeout_s=5,
    )


async def test_gemini_request_shape():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["headers"] = str(req.url), req.headers
        seen["body"] = json.loads(req.content)
        return _ok('{"item": {"x": 1}}')

    await _call(_provider(handler))
    assert seen["url"] == GEMINI_URL.format(model="gemini-2.5-flash")
    assert "secret-key-123" not in seen["url"]  # the key travels in a header, never the URL
    assert seen["headers"]["x-goog-api-key"] == "secret-key-123"
    body = seen["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "usr"}]}]
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    # $refs are inlined so the schema needs no $defs support.
    assert cfg["responseJsonSchema"]["properties"]["item"]["properties"]["x"] == {"type": "integer"}
    assert "$defs" not in json.dumps(cfg["responseJsonSchema"])
    assert cfg["maxOutputTokens"] >= 500  # plus room for the model's thinking tokens


async def test_gemini_parses_json_skipping_thoughts_and_counts_usage():
    r = await _call(_provider(lambda req: _ok('{"item": {"x": 2}}')))
    assert r.data == {"item": {"x": 2}}
    assert (r.input_tokens, r.output_tokens) == (11, 10)
    assert r.model == "gemini-2.5-flash"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "RATE_LIMITED", True),
        (500, "PROVIDER_ERROR", True),
        (503, "PROVIDER_ERROR", True),
        (400, "BAD_REQUEST", False),
        (403, "AUTH_FAILED", False),
        (404, "BAD_REQUEST", False),
    ],
)
async def test_gemini_error_mapping(status, code, retryable):
    p = _provider(lambda req: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ProviderError) as e:
        await _call(p)
    assert (e.value.code, e.value.retryable) == (code, retryable)
    assert "secret-key-123" not in e.value.message


async def test_gemini_400_with_bad_key_is_auth_failed():
    body = {"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}
    p = _provider(lambda req: httpx.Response(400, json=body))
    with pytest.raises(ProviderError) as e:
        await _call(p)
    assert (e.value.code, e.value.retryable) == ("AUTH_FAILED", False)


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY"])
async def test_gemini_incomplete_output_is_retryable_bad_output(finish):
    p = _provider(lambda req: _ok('{"item": ', finish=finish))
    with pytest.raises(ProviderError) as e:
        await _call(p)
    assert (e.value.code, e.value.retryable) == ("BAD_OUTPUT", True)


async def test_gemini_no_candidates_is_retryable_bad_output():
    p = _provider(lambda req: httpx.Response(200, json={"promptFeedback": {"blockReason": "X"}}))
    with pytest.raises(ProviderError) as e:
        await _call(p)
    assert (e.value.code, e.value.retryable) == ("BAD_OUTPUT", True)


async def test_gemini_timeout_is_retryable():
    def handler(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError) as e:
        await _call(_provider(handler))
    assert (e.value.code, e.value.retryable) == ("TIMEOUT", True)


@pytest.mark.parametrize(
    ("module", "cls"),
    [
        ("app.modules.agents.orchestrator.schema", "OrchestratorOutput"),
        ("app.modules.agents.requirements.agent", "RequirementsAgentOutput"),
        ("app.modules.agents.requirements.chat", "RequirementsChatOutput"),
        ("app.modules.agents.architecture.agent", "ArchitectureAgentOutput"),
        ("app.modules.agents.architecture.agent", "ChangeSetAgentOutput"),
        ("app.modules.agents.architecture.chat", "ArchitectureChatOutput"),
    ],
)
def test_every_agent_schema_inlines_without_refs(module, cls):
    import importlib

    from app.llm.gemini import inline_refs

    schema = getattr(importlib.import_module(module), cls).model_json_schema()
    assert "$ref" not in json.dumps(inline_refs(schema))
