"""Anthropic Claude (Messages API)."""

from __future__ import annotations

from app.llm.base import LLMConfigError, LLMError, LLMRequest, post_json

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicClient:
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
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.name = "Anthropic"
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
            f"{self.base_url}/v1/messages",
            headers={
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
                **self.extra_headers,
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            payload={
                "model": self.model,
                "max_tokens": 2500,
                "temperature": self.temperature,
                "system": request.system,
                "messages": [{"role": "user", "content": request.user}],
            },
            timeout=self.timeout,
            api_key=self.api_key,
            provider=self.name,
        )

        try:
            blocks = data["content"]
            text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        except (KeyError, TypeError):
            raise LLMError(f"{self.name}: неожиданная структура ответа") from None
        if not text.strip():
            raise LLMError(f"{self.name}: модель вернула пустой ответ")
        return text
