"""Provider-independent LLM contract. Agents depend on this, never on a vendor SDK."""

from typing import Any, Protocol

from pydantic import BaseModel


class LLMResult(BaseModel):
    data: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str


class ProviderError(Exception):
    """A provider call failed. ``retryable`` drives the worker's retry policy."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class LLMProvider(Protocol):
    name: str
    model: str
    is_mock: bool

    async def generate(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        timeout_s: float,
    ) -> LLMResult: ...
