"""Deterministic provider for tests and offline demos. Never allowed in production.

Runs made with it are flagged ``is_mock`` and labelled "Mock provider" in the UI.
"""

from collections.abc import Callable
from typing import Any

from app.llm.base import LLMResult, ProviderError

Script = Callable[[str, str, str], dict[str, Any] | Exception]


class MockProvider:
    name = "mock"
    model = "mock-1"
    is_mock = True

    def __init__(self, script: Script | None = None) -> None:
        self._script = script
        self.calls: list[dict[str, str]] = []

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
        self.calls.append({"schema_name": schema_name, "system": system, "user": user})
        if self._script is None:
            from app.llm.fixtures import default_output

            out: dict[str, Any] | Exception = default_output(schema_name, system, user)
        else:
            out = self._script(schema_name, system, user)
        if isinstance(out, Exception):
            raise out
        return LLMResult(data=out, input_tokens=len(user) // 4, output_tokens=100, model=self.model)


__all__ = ["MockProvider", "ProviderError"]
