"""Фабрика клиентов: собирает нужного провайдера по настройкам."""

from __future__ import annotations

from app.config import DEFAULT_MODELS
from app.llm.anthropic import AnthropicClient
from app.llm.base import LLMClient, LLMConfigError
from app.llm.google import GoogleClient
from app.llm.mock import MockClient
from app.llm.openai_compatible import OpenAICompatibleClient

SUPPORTED_PROVIDERS = ("openai", "anthropic", "google", "openai_compatible", "mock")


def create_client(
    *,
    provider: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    temperature: float = 0.2,
    timeout: float = 60.0,
    user_agent: str = "product-card/1.0",
    extra_headers: dict[str, str] | None = None,
) -> LLMClient:
    provider = (provider or "").strip().lower()
    model = model.strip() or DEFAULT_MODELS.get(provider, "")

    if provider == "mock":
        return MockClient(model="mock")

    common = {
        "api_key": api_key.strip(),
        "model": model,
        "base_url": base_url.strip(),
        "temperature": temperature,
        "timeout": timeout,
        "user_agent": user_agent,
        "extra_headers": extra_headers or {},
    }

    if provider == "openai":
        return OpenAICompatibleClient(name="OpenAI", **common)
    if provider == "openai_compatible":
        if not common["base_url"]:
            raise LLMConfigError(
                "Для OpenAI-совместимого провайдера укажите LLM_BASE_URL "
                "(например, https://opencode.ai/inference/go/openai/v1)"
            )
        return OpenAICompatibleClient(name="OpenAI-совместимый", **common)
    if provider == "anthropic":
        return AnthropicClient(**common)
    if provider == "google":
        return GoogleClient(**common)

    raise LLMConfigError(
        f"Неизвестный провайдер «{provider}». Доступны: {', '.join(SUPPORTED_PROVIDERS)}"
    )
