"""Google Gemini adapter (Generative Language REST API, JSON-schema structured output).

One ``generateContent`` request per call. The schema's ``$ref``s are inlined so only plain
JSON Schema keywords reach the API; callers always re-validate the result.
"""

import json
from typing import Any

import httpx

from app.llm.base import LLMResult, ProviderError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Gemini 2.5+ models think before answering, and thinking tokens count toward maxOutputTokens.
THINKING_ROOM = 4096

_STATUS = {
    400: ("BAD_REQUEST", False),
    401: ("AUTH_FAILED", False),
    403: ("AUTH_FAILED", False),
    404: ("BAD_REQUEST", False),
    413: ("TOO_LARGE", False),
    429: ("RATE_LIMITED", True),
}


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace local ``#/$defs/X`` references with their definitions and drop ``$defs``."""
    defs = schema.get("$defs", {})

    def walk(node: Any, depth: int = 0) -> Any:
        if depth > 40:
            raise ValueError("schema nesting too deep (recursive $ref?)")
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = walk(defs[ref.removeprefix("#/$defs/")], depth + 1)
                extra = {k: walk(v, depth + 1) for k, v in node.items() if k != "$ref"}
                return {**target, **extra}
            return {k: walk(v, depth + 1) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v, depth + 1) for v in node]
        return node

    return walk(schema)


def _api_key_rejected(resp: httpx.Response) -> bool:
    try:
        details = resp.json().get("error", {}).get("details", [])
    except ValueError:
        return False
    return any(d.get("reason") == "API_KEY_INVALID" for d in details if isinstance(d, dict))


class GeminiProvider:
    name = "gemini"
    is_mock = False

    def __init__(self, api_key: str, model: str, http: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self.model = model
        self._http = http or httpx.AsyncClient()

    async def generate(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        timeout_s: float,
    ) -> LLMResult:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": inline_refs(schema),
                "temperature": 0.2,
                "maxOutputTokens": max_output_tokens + THINKING_ROOM,
            },
        }
        try:
            resp = await self._http.post(
                GEMINI_URL.format(model=self.model),
                json=body,
                headers={"x-goog-api-key": self._key},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("TIMEOUT", "The model provider timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "NETWORK", "Couldn't reach the model provider", retryable=True
            ) from exc

        if resp.status_code >= 400:
            if resp.status_code == 400 and _api_key_rejected(resp):
                raise ProviderError(
                    "AUTH_FAILED", "The Gemini API key was rejected", retryable=False
                )
            code, retryable = _STATUS.get(resp.status_code, ("PROVIDER_ERROR", True))
            # Never echo the provider's body: it can contain request details.
            raise ProviderError(
                code,
                "The AI service is busy (rate limit reached)"
                if code == "RATE_LIMITED"
                else f"The AI service returned an error (HTTP {resp.status_code})",
                retryable=retryable,
            )

        try:
            payload = resp.json()
            candidate = payload["candidates"][0]
            if candidate.get("finishReason", "STOP") != "STOP":
                raise ValueError(f"generation stopped: {candidate.get('finishReason')}")
            parts = candidate["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("top-level JSON must be an object")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "BAD_OUTPUT", "The model returned output that isn't valid JSON", retryable=True
            ) from exc

        usage = payload.get("usageMetadata") or {}
        return LLMResult(
            data=data,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=(usage.get("candidatesTokenCount") or 0)
            + (usage.get("thoughtsTokenCount") or 0),
            model=self.model,
        )
