"""Справочник моделей и загрузка актуального списка у провайдера.

Справочник нужен, чтобы выпадающий список работал без ключа и без сети.
Кнопка «Обновить из API» тянет реальный список: названия моделей меняются
чаще, чем наш код.
"""

from __future__ import annotations

import hashlib
import time

import httpx

#: Курируемый список. Не истина в последней инстанции — только отправная точка.
CATALOG: dict[str, list[str]] = {
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o3-mini",
        "o4-mini",
    ],
    "anthropic": [
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "claude-opus-4-1",
        "claude-3-5-haiku-latest",
    ],
    "google": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ],
    "openai_compatible": [
        "deepseek-v4.1-flash",
        "deepseek-v4-pro",
        "glm-5.3",
        "glm-5.3-flash",
        "qwen3.8-flash",
        "kimi-k3",
        "gpt-5.6-luna",
        "claude-haiku-5-5",
    ],
    "mock": ["mock"],
}

CACHE_TTL_SECONDS = 3600
_cache: dict[str, tuple[float, list[str]]] = {}


class ModelListError(RuntimeError):
    """Не удалось получить список моделей у провайдера."""


def _cache_key(provider: str, base_url: str, api_key: str) -> str:
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
    return f"{provider}|{base_url}|{digest}"


def catalog_for(provider: str) -> list[str]:
    return list(CATALOG.get(provider, []))


async def _fetch_openai(
    api_key: str, base_url: str, timeout: float, extra_headers: dict[str, str]
) -> list[str]:
    url = f"{(base_url or 'https://api.openai.com/v1').rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "product-card/1.0",
                **extra_headers,
                "Authorization": f"Bearer {api_key}",
            },
        )
    if response.status_code in (401, 403):
        raise ModelListError("провайдер отклонил ключ")
    if response.status_code >= 400:
        raise ModelListError(f"HTTP {response.status_code}")
    data = response.json()
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ModelListError("неожиданный формат ответа")
    return sorted({str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")})


async def _fetch_anthropic(
    api_key: str, base_url: str, timeout: float, extra_headers: dict[str, str]
) -> list[str]:
    url = f"{(base_url or 'https://api.anthropic.com').rstrip('/')}/v1/models"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "product-card/1.0",
                **extra_headers,
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
    if response.status_code in (401, 403):
        raise ModelListError("провайдер отклонил ключ")
    if response.status_code >= 400:
        raise ModelListError(f"HTTP {response.status_code}")
    data = response.json()
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ModelListError("неожиданный формат ответа")
    return sorted({str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")})


async def _fetch_google(
    api_key: str, base_url: str, timeout: float, extra_headers: dict[str, str]
) -> list[str]:
    url = f"{(base_url or 'https://generativelanguage.googleapis.com/v1beta').rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "product-card/1.0",
                **extra_headers,
                "x-goog-api-key": api_key,
            },
        )
    if response.status_code in (401, 403):
        raise ModelListError("провайдер отклонил ключ")
    if response.status_code >= 400:
        raise ModelListError(f"HTTP {response.status_code}")
    data = response.json()
    items = data.get("models") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ModelListError("неожиданный формат ответа")
    names = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        names.append(name.removeprefix("models/"))
    return sorted({name for name in names if name})


_FETCHERS = {
    "openai": _fetch_openai,
    "openai_compatible": _fetch_openai,
    "anthropic": _fetch_anthropic,
    "google": _fetch_google,
}


async def fetch_models(
    provider: str,
    *,
    api_key: str,
    base_url: str = "",
    timeout: float = 10.0,
    force_refresh: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> list[str]:
    """Возвращает список моделей провайдера. Ошибки — исключением ModelListError."""
    fetcher = _FETCHERS.get(provider)
    if fetcher is None:
        raise ModelListError("для этого провайдера список моделей недоступен")

    cache_key = _cache_key(provider, base_url, api_key)
    if not force_refresh:
        cached = _cache.get(cache_key)
        if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
            return list(cached[1])

    models = await fetcher(api_key, base_url, timeout, extra_headers or {})
    if not models:
        raise ModelListError("провайдер вернул пустой список")

    _cache[cache_key] = (time.time(), models)
    return list(models)


def merge_with_catalog(provider: str, models: list[str]) -> list[str]:
    """Список от провайдера плюс справочник — если чего-то не хватает."""
    merged = list(models)
    for name in catalog_for(provider):
        if name not in merged:
            merged.append(name)
    return merged
