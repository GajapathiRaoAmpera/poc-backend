"""Groq adapter (OpenAI-compatible Chat Completions with JSON-schema structured output).

Structured outputs on Groq don't support streaming or tools, so each call is one request.
Best-effort schema mode is used; callers always re-validate the result.
"""

import copy
import json
import logging
from typing import Any

import httpx

from app.llm.base import LLMResult, ProviderError

logger = logging.getLogger(__name__)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Prompts here measure ~4.5 characters per token; the pre-flight only catches clear overruns
# (Groq's own 413 covers the borderline ones with the same error).
CHARS_PER_TOKEN = 4.5

_STATUS = {
    400: ("BAD_REQUEST", False),
    401: ("AUTH_FAILED", False),
    403: ("AUTH_FAILED", False),
    404: ("BAD_REQUEST", False),
    413: ("TOO_LARGE", False),
    422: ("BAD_REQUEST", False),
    429: ("RATE_LIMITED", True),
}


class GroqProvider:
    name = "groq"
    is_mock = False

    def __init__(
        self,
        api_key: str,
        model: str,
        http: httpx.AsyncClient | None = None,
        max_input_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._key = api_key
        self._http = http or httpx.AsyncClient()
        # Groq rejects any single request above the account's tokens-per-minute limit (413).
        self.max_input_tokens = max_input_tokens
        self.reasoning_effort = reasoning_effort
        self.model = f"{model}@{reasoning_effort}" if reasoning_effort else model
        self._model = model

    def with_effort(self, effort: str | None) -> "GroqProvider":
        """A copy that sends ``reasoning_effort``; shares the HTTP client."""
        clone = copy.copy(self)
        clone.reasoning_effort = effort
        clone.model = f"{self._model}@{effort}" if effort else self._model
        return clone

    def _too_large(self, estimate: int | None = None) -> ProviderError:
        size = f"about {estimate:,} tokens, " if estimate else ""
        limit = f"{self.max_input_tokens:,} tokens" if self.max_input_tokens else "its limit"
        return ProviderError(
            "TOO_LARGE",
            f"This step's input ({size}including the output schema) is over the model "
            f"provider's per-request token limit ({limit}). Use a model or plan with a higher "
            "limit, or shorten the project inputs.",
            retryable=False,
        )

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
        estimate = int((len(system) + len(user) + len(json.dumps(schema))) / CHARS_PER_TOKEN)
        if self.max_input_tokens and estimate > self.max_input_tokens:
            raise self._too_large(estimate)
        body = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema},
            },
            "temperature": 0.2,
            "max_completion_tokens": max_output_tokens,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
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
            # Server log only, never shown to users: Groq's reason (TPM, TPD, json_validate_failed).
            try:
                err = resp.json().get("error") or {}
            except ValueError:
                err = {}
            logger.warning(
                "Groq %s %s: type=%s code=%s retry_after=%s remaining_tokens=%s msg=%.300s",
                resp.status_code,
                self.model,
                err.get("type"),
                err.get("code"),
                resp.headers.get("retry-after"),
                resp.headers.get("x-ratelimit-remaining-tokens"),
                err.get("message"),
            )
            code, retryable = _STATUS.get(resp.status_code, ("PROVIDER_ERROR", True))
            if code == "TOO_LARGE":
                if self.max_input_tokens and estimate <= self.max_input_tokens:
                    # It fits on its own: the per-minute budget is shared with other calls.
                    raise ProviderError(
                        "RATE_LIMITED",
                        "The AI service's per-minute capacity is in use; retrying",
                        retryable=True,
                    )
                raise self._too_large()
            if code == "RATE_LIMITED" and "(TPD)" in str(err.get("message")):
                # Daily quota: per-minute retries can't succeed and only pile up failed runs.
                wait = resp.headers.get("retry-after")
                when = f" in about {int(wait) // 60 + 1} minutes" if wait and wait.isdigit() else " later"
                raise ProviderError(
                    "QUOTA_EXHAUSTED",
                    f"The AI service's daily token quota is used up. Try again{when}, "
                    "or switch to a different model or plan.",
                    retryable=False,
                )
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
