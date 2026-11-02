"""HTTP-интерфейс сервиса."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
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

#: Адреса, для которых разрешён http и локальная сеть (локальная разработка).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Не возвращаем клиенту исходные значения — в них может быть ключ API."""
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "field": ".".join(str(part) for part in error.get("loc", ())),
                    "message": error.get("msg", "некорректное значение"),
                }
                for error in exc.errors()
            ]
        },
    )


def _validate_base_url(raw: str) -> str:
    """Проверяет адрес endpoint'а: без него ключ можно увести на чужой хост."""
    parsed = urlparse(raw.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, detail="base_url должен быть полным URL вида https://host/v1")
    host = parsed.hostname
    if parsed.scheme == "http" and host not in LOCAL_HOSTS:
        raise HTTPException(400, detail="base_url: для внешних адресов используйте https")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        resolved = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        raise HTTPException(400, detail=f"base_url: не удалось разрешить хост «{host}»") from None

    for *_, sockaddr in resolved:
        address = ipaddress.ip_address(sockaddr[0])
        if address.is_loopback or address.is_private or address.is_link_local:
            if host not in LOCAL_HOSTS:
                raise HTTPException(
                    400,
                    detail="base_url: внутренние адреса запрещены",
                )
        if address.is_reserved or address.is_multicast:
            raise HTTPException(400, detail="base_url: служебные адреса запрещены")
    return raw.strip()


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

    requested_base_url = (options.base_url or "").strip()
    if requested_base_url:
        _validate_base_url(requested_base_url)

    same_provider = provider == settings.provider
    # Ключ из .env отправляем только на адрес из .env. Если клиент подставил
    # свой base_url, ключ обязан прийти вместе с запросом — иначе серверный
    # секрет утёк бы на чужой хост.
    uses_configured_endpoint = (
        not requested_base_url
        or requested_base_url.rstrip("/") == settings.base_url.rstrip("/")
    )
    api_key = (options.api_key or "").strip()
    if not api_key and same_provider and uses_configured_endpoint:
        api_key = settings.api_key

    base_url = requested_base_url or (settings.base_url if same_provider else "")

    return {
        "provider": provider,
        "api_key": api_key,
        "model": (options.model or (settings.model if same_provider else "")).strip()
        or DEFAULT_MODELS.get(provider, ""),
        "base_url": base_url,
        "temperature": options.temperature if options.temperature is not None else settings.temperature,
        "timeout": settings.timeout,
        "user_agent": settings.user_agent,
        "extra_headers": settings.extra_headers if uses_configured_endpoint else {},
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
