"""Настройки приложения: читаются из окружения и .env.

Ключи API никогда не попадают в код и в репозиторий. Их можно задать
в .env (файл в .gitignore) или ввести в веб-интерфейсе — тогда ключ живёт
только в памяти процесса на время запроса.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

#: Модели по умолчанию там, где название стабильно. Для Anthropic и Google
#: модель нужно указать явно: линейка моделей меняется чаще, чем наш код.
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "",
    "google": "",
    "openai_compatible": "deepseek-v4.1-flash",
    "mock": "mock",
}

PROVIDER_TITLES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "google": "Google Gemini",
    "openai_compatible": "OpenAI-совместимый endpoint",
    "mock": "Офлайн-демо (без модели)",
}


@dataclass(frozen=True)
class Settings:
    provider: str
    api_key: str
    model: str
    base_url: str
    timeout: float
    temperature: float
    user_agent: str = "product-card/1.0"
    extra_headers: dict[str, str] = field(default_factory=dict)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _headers_env(name: str) -> dict[str, str]:
    """Дополнительные HTTP-заголовки в виде JSON-объекта.

    Нужны для корпоративных шлюзов и прокси, которым требуется свой
    заголовок аутентификации или маршрутизации.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def load_settings() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "openai_compatible").strip() or "openai_compatible"
    return Settings(
        provider=provider,
        api_key=os.getenv("LLM_API_KEY", "").strip(),
        model=os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODELS.get(provider, ""),
        base_url=os.getenv("LLM_BASE_URL", "").strip(),
        timeout=_float_env("LLM_TIMEOUT", 60.0),
        temperature=_float_env("LLM_TEMPERATURE", 0.2),
        user_agent=os.getenv("LLM_USER_AGENT", "product-card/1.0").strip() or "product-card/1.0",
        extra_headers=_headers_env("LLM_EXTRA_HEADERS"),
    )
