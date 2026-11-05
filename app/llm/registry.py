from functools import lru_cache

from app.core.config import DEFAULT_GROQ_EFFORT, Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiProvider
from app.llm.groq import GroqProvider
from app.llm.mock import MockProvider

_override: LLMProvider | None = None


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        if settings.environment == "production":
            raise RuntimeError("LLM_PROVIDER=mock is not allowed when ENVIRONMENT=production")
        return MockProvider()
    if settings.llm_provider == "gemini":
        if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
            raise RuntimeError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        return GeminiProvider(settings.gemini_api_key.get_secret_value(), settings.gemini_model)
    if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value():
        raise RuntimeError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
    return GroqProvider(
        settings.groq_api_key.get_secret_value(),
        settings.groq_model,
        max_input_tokens=settings.groq_max_input_tokens,
    )


@lru_cache
def _default() -> LLMProvider:
    return build_provider(get_settings())


def get_provider() -> LLMProvider:
    return _override or _default()


def set_provider_for_tests(provider: LLMProvider | None) -> None:
    global _override
    _override = provider


_LOWER = {"high": "medium", "medium": "low", "low": "low"}


def effort_for(stage: str | None, mode: str | None, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    effort = {**DEFAULT_GROQ_EFFORT, **settings.groq_effort}.get(stage or "")
    if effort and mode == "chat" and settings.groq_chat_effort_drop:
        effort = _LOWER[effort]
    return effort


def for_stage(provider: LLMProvider, stage: str | None, mode: str | None) -> LLMProvider:
    """Tune the provider for one stage; providers without effort support pass through."""
    with_effort = getattr(provider, "with_effort", None)
    return with_effort(effort_for(stage, mode)) if with_effort else provider
