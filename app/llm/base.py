"""Общая часть для всех LLM-провайдеров."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LLMError(RuntimeError):
    """Понятная человеку ошибка обращения к модели."""


class LLMConfigError(LLMError):
    """Не хватает ключа, модели или адреса endpoint."""


class LLMResponseError(LLMError):
    """Модель вернула ответ, который не удалось разобрать."""


@dataclass
class LLMRequest:
    """Всё, что уходит в модель, включая структурированные данные товара."""

    system: str
    user: str
    payload: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    name: str
    model: str

    async def complete_json(self, request: LLMRequest) -> str:
        """Возвращает сырой текстовый ответ модели (ожидается JSON)."""


def parse_json_object(text: str) -> dict[str, Any]:
    """Достаёт JSON-объект из ответа модели, даже если тот обёрнут в ```json."""
    cleaned = FENCE_RE.sub("", text.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMResponseError("Модель вернула ответ без JSON-объекта") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"Не удалось разобрать JSON от модели: {exc.msg}") from None
    if not isinstance(parsed, dict):
        raise LLMResponseError("Ожидался JSON-объект, получено что-то другое")
    return parsed


def redact(text: str, secret: str) -> str:
    """Убирает ключ из текста ошибки, чтобы он не попал в логи и в ответ API."""
    if secret and len(secret) >= 8:
        return text.replace(secret, "***")
    return text


async def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    api_key: str,
    provider: str,
    attempts: int = 2,
) -> dict[str, Any]:
    """POST с повтором на сетевые ошибки, 429 и 5xx."""
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            last_error = f"{provider}: превышено время ожидания ответа ({timeout:.0f} с)"
        except httpx.HTTPError as exc:
            last_error = f"{provider}: сетевая ошибка — {redact(str(exc), api_key)}"
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError:
                    raise LLMResponseError(f"{provider}: ответ не является JSON") from None
            body = redact(response.text[:400], api_key)
            if response.status_code in (401, 403):
                raise LLMError(f"{provider}: провайдер отклонил ключ (HTTP {response.status_code}). {body}")
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"{provider}: временная ошибка HTTP {response.status_code}. {body}"
            else:
                raise LLMError(f"{provider}: HTTP {response.status_code}. {body}")

        if attempt < attempts:
            await asyncio.sleep(1.5 * attempt)

    raise LLMError(last_error or f"{provider}: не удалось получить ответ")
