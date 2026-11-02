"""Google Gemini (generateContent)."""

from __future__ import annotations

from app.llm.base import LLMConfigError, LLMError, LLMRequest, post_json

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


class GoogleClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
        temperature: float = 0.2,
        timeout: float = 60.0,
        user_agent: str = "product-card/1.0",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or API_ROOT).rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.name = "Google"
        self.user_agent = user_agent
        self.extra_headers = extra_headers or {}

    async def complete_json(self, request: LLMRequest) -> str:
        if not self.api_key:
            raise LLMConfigError(
                f"{self.name}: не задан ключ API. Укажите LLM_API_KEY в .env или введите ключ в форме."
            )
        if not self.model:
            raise LLMConfigError(f"{self.name}: не задана модель")

        data = await post_json(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
                **self.extra_headers,
                "x-goog-api-key": self.api_key,
            },
            payload={
                "systemInstruction": {"parts": [{"text": request.system}]},
                "contents": [{"role": "user", "parts": [{"text": request.user}]}],
                "generationConfig": {
                    "temperature": self.temperature,
                    "responseMimeType": "application/json",
                },
            },
            timeout=self.timeout,
            api_key=self.api_key,
            provider=self.name,
        )

        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"{self.name}: неожиданная структура ответа") from None
        if not text.strip():
            raise LLMError(f"{self.name}: модель вернула пустой ответ")
        return text
