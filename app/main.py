"""HTTP-интерфейс сервиса."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import DEFAULT_MODELS, PROVIDER_TITLES, load_settings
from app.llm.base import LLMConfigError, LLMError, LLMResponseError
from app.llm.factory import SUPPORTED_PROVIDERS, create_client
from app.model_catalog import ModelListError, catalog_for, fetch_models, merge_with_catalog
from app.models import CompletenessReport, ProductInput
from app.pipeline import analyze_product, generate_card
from app.render import to_json
from app.sessions import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    ClientSession,
    session_view,
    store,
)

logger = logging.getLogger("product_card")

BASE_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
EXAMPLES_DIR = BASE_DIR / "examples"

TAGS_METADATA = [
    {
        "name": "карточка",
        "description": "Проверка данных и генерация черновика карточки товара.",
    },
    {
        "name": "служебное",
        "description": "Провайдеры, примеры данных и проверка живости сервиса.",
    },
]

app = FastAPI(
    title="AI-помощник для подготовки товарных описаний",
    description=(
        "Сервис собирает черновик карточки товара для B2B-магазина КИПиА: "
        "SEO-заголовок, описание, характеристики, поисковые запросы и коммерческий блок.\n\n"
        "**Технические значения берутся только из входных данных.** Ответ модели "
        "проверяется кодом: числа сверяются по токенам, типовые утверждения — по словарю, "
        "значения характеристик подставляются из `specs` дословно.\n\n"
        "Ключ API можно не передавать в запросе — тогда используется ключ из `.env`. "
        "Ключ, переданный в запросе, нигде не сохраняется."
    ),
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "product": {
                        "name": "Манометр показывающий МП3-У",
                        "manufacturer": "Манотомь",
                        "specs": {
                            "Диапазон измерений": "0–100 кгс/см²",
                            "Класс точности": "1,5",
                            "Диаметр корпуса": "100 мм",
                        },
                        "description": "Прибор для измерения избыточного давления.",
                    },
                    "llm": {"provider": "mock"},
                }
            ]
        }
    )

    product: dict[str, Any] = Field(description="Данные товара: name, manufacturer, specs, description")
    llm: LLMOptions = LLMOptions()


class AnalyzeRequest(BaseModel):
    product: dict[str, Any]


class SessionRequest(BaseModel):
    """Сохранение настроек и ключа в серверной сессии."""

    provider: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    model: str | None = None
    base_url: str | None = None


class ModelsRequest(BaseModel):
    provider: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    base_url: str | None = None
    refresh: bool = False


#: Человеческие названия полей — сообщения читает контент-менеджер, а не разработчик.
FIELD_TITLES = {
    "name": "Название товара",
    "manufacturer": "Производитель",
    "specs": "Характеристики",
    "description": "Описание",
}


def _humanize_error(error: dict[str, Any]) -> dict[str, str]:
    """Переводит ошибку валидатора в понятную фразу на русском."""
    loc = [str(part) for part in error.get("loc", ()) if part != "product"]
    field = ".".join(loc) or "product"
    title = FIELD_TITLES.get(loc[0] if loc else "", "Поле")
    error_type = error.get("type", "")
    limits = error.get("ctx") or {}

    if error_type == "string_too_short":
        message = "не может быть пустым"
    elif error_type == "string_too_long":
        message = f"слишком длинное значение, максимум {limits.get('max_length')} символов"
    elif error_type == "missing":
        message = "обязательное поле не заполнено"
    elif error_type == "string_type":
        message = "ожидается текст"
    elif error_type in {"dict_type", "list_type"}:
        message = "ожидается объект или список пар «название — значение»"
    elif error_type == "value_error":
        message = str(error.get("msg", "")).removeprefix("Value error, ")
    else:
        message = "некорректное значение"

    return {"field": field, "title": title, "message": message}


def _parse_product(raw: dict[str, Any]) -> ProductInput:
    try:
        return ProductInput.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[_humanize_error(error) for error in exc.errors()],
        ) from None


def _resolve_options(options: LLMOptions, session: ClientSession | None = None) -> dict[str, Any]:
    settings = load_settings()
    provider = (
        options.provider
        or (session.provider if session else "")
        or settings.provider
    ).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный провайдер «{provider}». Доступны: {', '.join(SUPPORTED_PROVIDERS)}",
        )

    # Адрес endpoint'а: запрос → сессия → .env. Пользовательский адрес проверяем.
    requested_base_url = (options.base_url or "").strip()
    if requested_base_url:
        _validate_base_url(requested_base_url)
    base_url = requested_base_url
    if not base_url and session and session.base_url and (not session.provider or session.provider == provider):
        base_url = session.base_url
    if not base_url and provider == settings.provider:
        base_url = settings.base_url

    same_provider = provider == settings.provider
    uses_configured_endpoint = (
        not base_url or base_url.rstrip("/") == settings.base_url.rstrip("/")
    )

    # Ключ: запрос → сессия (только для её endpoint'а) → .env (только для своего).
    api_key = (options.api_key or "").strip()
    if not api_key and session and session.api_key:
        session_matches = (
            (not session.provider or session.provider == provider)
            and (session.base_url or "").rstrip("/") == (base_url or "").rstrip("/")
        )
        if session_matches:
            api_key = session.api_key
    if not api_key and same_provider and uses_configured_endpoint:
        api_key = settings.api_key

    model = (options.model or "").strip()
    if not model and session and session.model and (not session.provider or session.provider == provider):
        model = session.model
    if not model and same_provider:
        model = settings.model
    if not model:
        model = DEFAULT_MODELS.get(provider, "")

    return {
        "provider": provider,
        "api_key": api_key,
        "model": model,
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


@app.get("/health", tags=["служебное"], summary="Проверка живости")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/providers", tags=["служебное"], summary="Доступные провайдеры LLM")
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


@app.get("/api/examples", tags=["служебное"], summary="Примеры данных товаров")
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


def _session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )


@app.get("/api/session", tags=["служебное"], summary="Что сохранено в сессии")
def read_session(
    pcard_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    return session_view(store.get(pcard_session))


@app.post("/api/session", tags=["служебное"], summary="Сохранить ключ и настройки в сессии")
def save_session(
    payload: SessionRequest,
    response: Response,
    pcard_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    session = store.get(pcard_session)
    session_id = pcard_session if session is not None else store.new_id()
    if payload.base_url:
        _validate_base_url(payload.base_url)
    saved = store.update(
        session_id,
        provider=(payload.provider or "").strip().lower() or None,
        api_key=(payload.api_key or "").strip() or None,
        model=payload.model.strip() if payload.model is not None else None,
        base_url=payload.base_url.strip() if payload.base_url is not None else None,
    )
    _session_cookie(response, session_id)
    return session_view(saved)


@app.delete("/api/session", tags=["служебное"], summary="Забыть сохранённый ключ")
def clear_session(
    response: Response,
    pcard_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    store.clear(pcard_session)
    response.delete_cookie(SESSION_COOKIE)
    return session_view(None)


@app.post("/api/models", tags=["служебное"], summary="Список моделей провайдера")
async def models(
    payload: ModelsRequest,
    pcard_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    settings = load_settings()
    session = store.get(pcard_session)
    provider = (
        payload.provider or (session.provider if session else "") or settings.provider
    ).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Неизвестный провайдер «{provider}»")

    base_url = (payload.base_url or "").strip()
    if base_url:
        _validate_base_url(base_url)
    elif session and session.base_url and (not session.provider or session.provider == provider):
        base_url = session.base_url

    api_key = (payload.api_key or "").strip()
    if not api_key and session and session.api_key:
        if (not session.provider or session.provider == provider) and (
            (session.base_url or "").rstrip("/") == base_url.rstrip("/")
        ):
            api_key = session.api_key
    if not api_key and provider == settings.provider and (
        not base_url or base_url.rstrip("/") == settings.base_url.rstrip("/")
    ):
        api_key = settings.api_key

    catalog = catalog_for(provider)
    if provider == "mock" or not api_key:
        return {
            "provider": provider,
            "models": catalog,
            "source": "catalog",
            "note": "" if provider == "mock" else "Укажите ключ, чтобы загрузить полный список моделей",
        }

    try:
        models_list = await fetch_models(
            provider,
            api_key=api_key,
            base_url=base_url,
            timeout=min(settings.timeout, 15.0),
            force_refresh=payload.refresh,
            extra_headers=(
                settings.extra_headers
                if not base_url or base_url.rstrip("/") == settings.base_url.rstrip("/")
                else {}
            ),
        )
    except ModelListError as exc:
        return {
            "provider": provider,
            "models": catalog,
            "source": "catalog",
            "note": f"Не удалось получить список у провайдера: {exc}. Показан справочник.",
        }

    return {
        "provider": provider,
        "models": merge_with_catalog(provider, models_list),
        "source": "provider",
        "note": "",
    }


@app.post(
    "/api/analyze",
    response_model=CompletenessReport,
    tags=["карточка"],
    summary="Проверить полноту данных",
    description=(
        "Определяет семейство прибора и перечисляет характеристики, которых не хватает. "
        "Модель не вызывается: проверка детерминированная и бесплатная."
    ),
)
def analyze(payload: AnalyzeRequest) -> CompletenessReport:
    """Проверка полноты данных до обращения к модели."""
    product = _parse_product(payload.product)
    return analyze_product(product)


@app.post(
    "/api/generate",
    tags=["карточка"],
    summary="Сформировать карточку товара",
    description=(
        "Возвращает карточку в трёх видах: `card` (структура), `markdown` (готовый текст "
        "для копирования) и `json` (та же карточка строкой). В `meta` — провайдер, модель, "
        "время и число обращений к модели.\n\n"
        "Провайдер `mock` собирает карточку по шаблону без обращения к внешнему API."
    ),
    responses={
        400: {"description": "Неизвестный провайдер, не задан ключ или небезопасный base_url"},
        422: {"description": "Некорректные данные товара"},
        502: {"description": "Ошибка или недоступность провайдера LLM"},
    },
)
async def generate(
    payload: GenerateRequest,
    pcard_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    product = _parse_product(payload.product)
    options = _resolve_options(payload.llm, store.get(pcard_session))

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
        "text": result.text,
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
