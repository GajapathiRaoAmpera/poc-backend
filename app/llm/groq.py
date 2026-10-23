"""Groq adapter (OpenAI-compatible Chat Completions with JSON-schema structured output).

Structured outputs on Groq don't support streaming or tools, so each call is one request.
Best-effort schema mode is used; callers always re-validate the result.
"""

import json
from typing import Any

import httpx

from app.llm.base import LLMResult, ProviderError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_STATUS = {
    400: ("BAD_REQUEST", False),
    401: ("AUTH_FAILED", False),
    403: ("AUTH_FAILED", False),
    404: ("BAD_REQUEST", False),
    413: ("BAD_REQUEST", False),
    422: ("BAD_REQUEST", False),
    429: ("RATE_LIMITED", True),
}


class GroqProvider:
    name = "groq"
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
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema},
            },
            "temperature": 0.2,
            "max_completion_tokens": max_output_tokens,
        }
        try:
            resp = await self._http.post(
                GROQ_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("TIMEOUT", "The model provider timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "NETWORK", "Couldn't reach the model provider", retryable=True
            ) from exc

        if resp.status_code >= 400:
            code, retryable = _STATUS.get(resp.status_code, ("PROVIDER_ERROR", True))
            # Never echo the provider's body: it can contain request details.
            raise ProviderError(
                code, f"Model provider returned HTTP {resp.status_code}", retryable=retryable
            )

        try:
            payload = resp.json()
            content = payload["choices"][0]["message"]["content"]
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("top-level JSON must be an object")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "BAD_OUTPUT", "The model returned output that isn't valid JSON", retryable=True
            ) from exc

        usage = payload.get("usage") or {}
        return LLMResult(
            data=data,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            model=payload.get("model", self.model),
        )
