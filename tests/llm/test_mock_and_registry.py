import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.llm.base import ProviderError
from app.llm.mock import MockProvider
from app.llm.registry import build_provider


async def test_mock_returns_scripted_output_and_marks_itself():
    p = MockProvider(script=lambda name, system, user: {"echo": name})
    r = await p.generate(
        system="s", user="u", schema={}, schema_name="abc", max_output_tokens=10, timeout_s=1
    )
    assert r.data == {"echo": "abc"}
    assert p.name == "mock" and p.is_mock is True


async def test_mock_can_raise_scripted_errors():
    p = MockProvider(script=lambda *a: ProviderError("RATE_LIMITED", "slow down", retryable=True))
    with pytest.raises(ProviderError):
        await p.generate(
            system="s", user="u", schema={}, schema_name="x", max_output_tokens=10, timeout_s=1
        )


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, mongodb_uri="mongodb://x", **kw)


def test_mock_refused_in_production():
    with pytest.raises(RuntimeError, match="mock"):
        build_provider(_settings(environment="production", llm_provider="mock"))


def test_groq_requires_key():
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        build_provider(_settings(environment="production", llm_provider="groq"))


def test_groq_built_with_key_and_model():
    p = build_provider(
        _settings(
            environment="production",
            llm_provider="groq",
            groq_api_key=SecretStr("k"),
            groq_model="openai/gpt-oss-20b",
        )
    )
    assert (p.name, p.model, p.is_mock) == ("groq", "openai/gpt-oss-20b", False)


def test_gemini_requires_key():
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_provider(_settings(environment="production", llm_provider="gemini"))


def test_gemini_built_with_key_and_model():
    p = build_provider(
        _settings(
            environment="production",
            llm_provider="gemini",
            gemini_api_key=SecretStr("k"),
            gemini_model="gemini-2.5-pro",
        )
    )
    assert (p.name, p.model, p.is_mock) == ("gemini", "gemini-2.5-pro", False)
