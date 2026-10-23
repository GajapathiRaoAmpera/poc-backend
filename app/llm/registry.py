from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.groq import GroqProvider
from app.llm.mock import MockProvider

_override: LLMProvider | None = None


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        if settings.environment == "production":
            raise RuntimeError("LLM_PROVIDER=mock is not allowed when ENVIRONMENT=production")
        return MockProvider()
    if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value():
        raise RuntimeError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
    return GroqProvider(settings.groq_api_key.get_secret_value(), settings.groq_model)


@lru_cache
def _default() -> LLMProvider:
    return build_provider(get_settings())


def get_provider() -> LLMProvider:
    return _override or _default()


def set_provider_for_tests(provider: LLMProvider | None) -> None:
    global _override
    _override = provider
