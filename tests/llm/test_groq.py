import json

import httpx
import pytest

from app.llm.base import ProviderError
from app.llm.groq import GROQ_URL, GroqProvider

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def _provider(handler) -> GroqProvider:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GroqProvider(api_key="secret-key-123", model="openai/gpt-oss-120b", http=http)


def _ok(content: str, usage=None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7},
            "model": "openai/gpt-oss-120b",
        },
    )


async def _call(p: GroqProvider):
    return await p.generate(
        system="sys",
        user="usr",
        schema=SCHEMA,
        schema_name="thing",
        max_output_tokens=500,
        timeout_s=5,
    )


async def test_groq_request_shape():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return _ok('{"x": 1}')

    await _call(_provider(handler))
    assert seen["url"] == GROQ_URL == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key-123"
    body = seen["body"]
    assert body["model"] == "openai/gpt-oss-120b"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "thing"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["temperature"] == 0.2
    assert body["max_completion_tokens"] == 500


async def test_groq_parses_json_and_usage():
    r = await _call(_provider(lambda req: _ok('{"x": 42}')))
    assert r.data == {"x": 42}
    assert (r.input_tokens, r.output_tokens) == (11, 7)
    assert r.model == "openai/gpt-oss-120b"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "RATE_LIMITED", True),
        (500, "PROVIDER_ERROR", True),
        (503, "PROVIDER_ERROR", True),
        (400, "BAD_REQUEST", False),
        (401, "AUTH_FAILED", False),
    ],
)
async def test_groq_error_mapping(status, code, retryable):
    p = _provider(lambda req: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ProviderError) as e:
        await _call(p)
    assert (e.value.code, e.value.retryable) == (code, retryable)


async def test_groq_timeout_is_retryable():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderError) as e:
        await _call(_provider(handler))
    assert (e.value.code, e.value.retryable) == ("TIMEOUT", True)


async def test_groq_non_json_content_is_retryable_bad_output():
    with pytest.raises(ProviderError) as e:
        await _call(_provider(lambda req: _ok("not json at all")))
    assert (e.value.code, e.value.retryable) == ("BAD_OUTPUT", True)


async def test_key_never_in_error_message():
    p = _provider(lambda req: httpx.Response(401, text="invalid key secret-key-123"))
    with pytest.raises(ProviderError) as e:
        await _call(p)
    assert "secret-key-123" not in str(e.value) and "secret-key-123" not in e.value.message
