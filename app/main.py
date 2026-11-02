"""HTTP-интерфейс сервиса."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.config import DEFAULT_MODELS, PROVIDER_TITLES, load_settings
from app.llm.base import LLMConfigError, LLMError, LLMResponseError
from app.llm.factory import SUPPORTED_PROVIDERS, create_client
from app.models import CompletenessReport, ProductInput
from app.pipeline import analyze_product, generate_card
from app.render import to_json

logger = logging.getLogger("product_card")

BASE_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
EXAMPLES_DIR = BASE_DIR / "examples"

app = FastAPI(
    title="AI-помощник для подготовки товарных описаний",
    description="Черновик карточки товара для B2B-магазина КИПиА по данным прибора.",
    version="1.0.0",
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class LLMOptions(BaseModel):
    """Параметры провайдера. Ключ приходит из формы и нигде не сохраняется."""

    provider: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    model: str | None = None
    base_url: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class GenerateRequest(BaseModel):
    product: dict[str, Any]
    llm: LLMOptions = LLMOptions()


class AnalyzeRequest(BaseModel):
    product: dict[str, Any]


def _parse_product(raw: dict[str, Any]) -> ProductInput:
    try:
        return ProductInput.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "field": ".".join(str(part) for part in error["loc"]) or "product",
                    "message": error["msg"],
                }
                for error in exc.errors()
            ],
        ) from None


def _resolve_options(options: LLMOptions) -> dict[str, Any]:
    settings = load_settings()
    provider = (options.provider or settings.provider).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный провайдер «{provider}». Доступны: {', '.join(SUPPORTED_PROVIDERS)}",
        )
    same_provider = provider == settings.provider
    return {
        "provider": provider,
        "api_key": (options.api_key or (settings.api_key if same_provider else "")).strip(),
        "model": (options.model or (settings.model if same_provider else "")).strip()
        or DEFAULT_MODELS.get(provider, ""),
        "base_url": (options.base_url or (settings.base_url if same_provider else "")).strip(),
        "temperature": options.temperature if options.temperature is not None else settings.temperature,
        "timeout": settings.timeout,
        "user_agent": settings.user_agent,
        "extra_headers": settings.extra_headers,
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=500, detail="Не найден файл интерфейса")
    return FileResponse(page)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/providers")
def providers() -> dict[str, Any]:
    return {
        "providers": [
            {
                "id": provider,
                "title": PROVIDER_TITLES.get(provider, provider),
                "default_model": DEFAULT_MODELS.get(provider, ""),
                "needs_key": provider != "mock",
                "needs_base_url": provider == "openai_compatible",
            }
            for provider in SUPPORTED_PROVIDERS
        ]
    }


@app.get("/api/examples")
def examples() -> dict[str, Any]:
    """Готовые примеры из каталога examples/ для кнопки «Пример JSON»."""
    items: list[dict[str, Any]] = []
    if EXAMPLES_DIR.is_dir():
        for path in sorted(EXAMPLES_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append(
                {
                    "id": path.stem,
                    "title": str(payload.get("name") or path.stem),
                    "payload": payload,
                }
            )
    return {"examples": items}


@app.post("/api/analyze", response_model=CompletenessReport)
def analyze(payload: AnalyzeRequest) -> CompletenessReport:
    """Проверка полноты данных до обращения к модели."""
    product = _parse_product(payload.product)
    return analyze_product(product)


@app.post("/api/generate")
async def generate(payload: GenerateRequest) -> dict[str, Any]:
    product = _parse_product(payload.product)
    options = _resolve_options(payload.llm)

    try:
        client = create_client(**options)
        result = await generate_card(
            product,
            client,
            provider_title=PROVIDER_TITLES.get(options["provider"], options["provider"]),
        )
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except LLMError as exc:
        logger.warning("Ошибка провайдера: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return {
        "card": result.card.model_dump(),
        "markdown": result.markdown,
        "json": to_json(result.card),
        "meta": {
            "provider": options["provider"],
            "provider_title": result.provider,
            "model": result.model,
            "family": result.family_title,
            "attempts": result.attempts,
            "elapsed_ms": result.elapsed_ms,
        },
    }
