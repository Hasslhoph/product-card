"""OpenAI и любой OpenAI-совместимый endpoint (в том числе локальные модели)."""

from __future__ import annotations

from typing import Any

from app.llm.base import LLMConfigError, LLMError, LLMRequest, post_json


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
        temperature: float = 0.2,
        timeout: float = 60.0,
        name: str = "OpenAI",
        user_agent: str = "product-card/1.0",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.name = name
        self.user_agent = user_agent
        self.extra_headers = extra_headers or {}

    async def complete_json(self, request: LLMRequest) -> str:
        if not self.api_key:
            raise LLMConfigError(
                f"{self.name}: не задан ключ API. Укажите LLM_API_KEY в .env или введите ключ в форме."
            )
        if not self.model:
            raise LLMConfigError(f"{self.name}: не задана модель")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "User-Agent": self.user_agent}
        headers.update(self.extra_headers)
        headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            data = await post_json(
                url,
                headers=headers,
                payload=payload,
                timeout=self.timeout,
                api_key=self.api_key,
                provider=self.name,
            )
        except LLMError as exc:
            # Не все совместимые endpoint'ы поддерживают response_format.
            # Повторяем без него только при явной жалобе на этот параметр,
            # чтобы не маскировать настоящую ошибку запроса.
            message = str(exc).lower()
            if not any(
                marker in message
                for marker in ("response_format", "response format", "json_object")
            ):
                raise
            payload.pop("response_format", None)
            data = await post_json(
                url,
                headers=headers,
                payload=payload,
                timeout=self.timeout,
                api_key=self.api_key,
                provider=self.name,
            )

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"{self.name}: неожиданная структура ответа") from None
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"{self.name}: модель вернула пустой ответ")
        return content
