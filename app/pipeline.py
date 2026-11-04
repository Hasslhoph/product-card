"""Конвейер подготовки карточки товара.

Порядок шагов намеренно такой: сначала проверяем данные, потом просим
модель, а затем проверяем её ответ кодом. Ни один факт не попадает в
карточку только потому, что модель его «знает».
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.builder import build_characteristics
from app.core.guard import (
    find_unsupported_claims,
    find_unsupported_numbers,
    sanitize_unsupported,
)
from app.core.profiles import detect_profile, find_missing_fields, get_profile
from app.llm.base import LLMClient, LLMError, LLMRequest, parse_json_object
from app.models import (
    MISSING_VALUE,
    CompletenessReport,
    ProductCard,
    ProductInput,
    SpecItem,
)
from app.render import DESC_MAX, DESC_MIN, TITLE_LIMIT, to_markdown, to_plain_text

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
USER_TEMPLATE = (PROMPTS_DIR / "user.md").read_text(encoding="utf-8")

QUERIES_MIN = 3
QUERIES_MAX = 5
COMMERCIAL_MIN = 80

#: Нейтральные фразы без единого числа — ими добиваем описание до нижней
#: границы, если модель написала слишком коротко. Фактов они не добавляют.
NEUTRAL_SENTENCES = (
    "Характеристики, не указанные во входных данных, в карточке не приводятся.",
    "Уточнить исполнение и комплектацию прибора можно у менеджера.",
    "Карточка составлена по данным поставщика.",
    "Перед заказом рекомендуется проверить совместимость с имеющимся оборудованием.",
)

_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_LIST_MARK_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


@dataclass
class GenerationResult:
    card: ProductCard
    markdown: str
    text: str
    provider: str
    model: str
    attempts: int
    elapsed_ms: int
    family_title: str


# --------------------------------------------------------------------------
# Проверка входных данных (без обращения к модели)
# --------------------------------------------------------------------------


def analyze_product(product: ProductInput) -> CompletenessReport:
    profile = detect_profile(product.name, product.description)
    missing_keys = find_missing_fields(product.specs, profile, product.description)
    missing_set = set(missing_keys)
    found = [field.title for field in profile.fields if field.key not in missing_set]
    titles = {field.key: field.title for field in profile.fields}
    missing = [titles.get(key, key) for key in missing_keys]

    warnings: list[str] = []
    if not product.specs:
        warnings.append("Не переданы характеристики — карточка будет состоять только из названия и описания.")
    if not product.description:
        warnings.append("Не передано описание или фрагмент техпаспорта.")
    if not product.manufacturer:
        warnings.append("Не указан производитель.")

    return CompletenessReport(
        family=profile.id,
        family_title=profile.title,
        present=found,
        missing=missing,
        warnings=warnings,
        can_generate=bool(product.specs or product.description),
    )


# --------------------------------------------------------------------------
# Промпты
# --------------------------------------------------------------------------


def build_request(product: ProductInput, profile_title: str, missing: list[str]) -> LLMRequest:
    product_json = json.dumps(product.model_dump(), ensure_ascii=False, indent=2)
    # Данные товара не должны уметь «закрыть» блок кода и дописать инструкции.
    product_json = product_json.replace("```", "'''")
    user = USER_TEMPLATE.format(
        product_json=product_json,
        family_title=profile_title,
        missing_fields="; ".join(missing) if missing else "нет",
    )
    return LLMRequest(system=SYSTEM_PROMPT, user=user, payload={"product": product.model_dump()})


def build_repair_request(request: LLMRequest, problems: list[str]) -> LLMRequest:
    feedback = (
        "\n\n---\n\n"
        "ПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРОШЁЛ ПРОВЕРКУ:\n"
        + "\n".join(f"- {problem}" for problem in problems)
        + "\n\nИсправь перечисленные проблемы и верни JSON целиком, в том же формате. "
        "Не добавляй факты, которых нет в данных товара."
    )
    return LLMRequest(system=request.system, user=request.user + feedback, payload=request.payload)


# --------------------------------------------------------------------------
# Нормализация и проверки ответа модели
# --------------------------------------------------------------------------


def _clean_line(value: Any) -> str:
    text = str(value or "").replace("\r", "")
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip().strip('"«»“”').strip()


def _clean_block(value: Any) -> str:
    lines: list[str] = []
    for raw_line in str(value or "").replace("\r", "").split("\n"):
        line = _LIST_MARK_RE.sub("", raw_line)
        line = _clean_line(line)
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_queries(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[\n;]+", value)
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = []

    result: list[str] = []
    for part in parts:
        query = _clean_line(part).strip("-•*").strip('"')
        query = re.sub(r"^\d+[.)]\s*", "", query)
        if query and query not in result:
            result.append(query)
    return result


def _normalize_card(
    data: dict[str, Any], product: ProductInput, profile_id: str
) -> tuple[ProductCard, list[str]]:
    characteristics, warnings = build_characteristics(
        data.get("characteristics") or [], product.specs, profile_id
    )
    card = ProductCard(
        seo_title=_clean_line(data.get("seo_title")),
        seo_description=_clean_block(data.get("seo_description")),
        characteristics=characteristics,
        search_queries=_clean_queries(data.get("search_queries")),
        commercial_block=_clean_block(data.get("commercial_block")),
    )
    return card, warnings


def collect_problems(
    card: ProductCard,
    source: str,
    *,
    name: str = "",
    claim_source: str | None = None,
) -> list[str]:
    """Формальные нарушения контракта карточки, которые видны без семантики."""
    problems: list[str] = []

    if not card.seo_title:
        problems.append("поле seo_title пустое")
    elif len(card.seo_title) > TITLE_LIMIT:
        problems.append(f"seo_title длиннее {TITLE_LIMIT} символов (сейчас {len(card.seo_title)})")

    if not card.seo_description:
        problems.append("поле seo_description пустое")
    elif len(card.seo_description) < DESC_MIN:
        problems.append(
            f"seo_description короче {DESC_MIN} символов (сейчас {len(card.seo_description)})"
        )
    elif len(card.seo_description) > DESC_MAX:
        problems.append(
            f"seo_description длиннее {DESC_MAX} символов (сейчас {len(card.seo_description)})"
        )

    if not QUERIES_MIN <= len(card.search_queries) <= QUERIES_MAX:
        problems.append(
            f"поисковых запросов должно быть от {QUERIES_MIN} до {QUERIES_MAX} "
            f"(сейчас {len(card.search_queries)})"
        )

    if len(card.commercial_block) < COMMERCIAL_MIN:
        problems.append("коммерческий блок слишком короткий")

    if not card.characteristics:
        problems.append("список характеристик пустой")

    text = " ".join(
        [
            card.seo_title,
            card.seo_description,
            card.commercial_block,
            " ".join(card.search_queries),
        ]
    )
    unsupported = find_unsupported_numbers(text, source, name=name)
    unsupported_claims = find_unsupported_claims(
        text, claim_source if claim_source is not None else source
    )
    if unsupported:
        problems.append(
            "в тексте есть значения, которых нет во входных данных: " + ", ".join(unsupported)
        )
    if unsupported_claims:
        problems.append(
            "в тексте есть утверждения, которых нет во входных данных: "
            + ", ".join(unsupported_claims)
        )

    return problems


def _clip(text: str, limit: int) -> str:
    """Обрезает строку по границе слова, не оставляя висящей запятой."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" -—–,;:.")


def _truncate_title(title: str, fallback: str) -> str:
    title = title.strip() or fallback.strip()
    clipped = _clip(title, TITLE_LIMIT)
    return clipped or _clip(fallback.strip(), TITLE_LIMIT)


def _fit_description(text: str) -> tuple[str, bool]:
    """Приводит описание к диапазону 800–1200 символов. Возвращает (текст, добито ли)."""
    if len(text) > DESC_MAX:
        cut = text[:DESC_MAX]
        boundary = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("\n\n"))
        if boundary > DESC_MIN:
            cut = cut[: boundary + 1]
        else:
            cut = cut[: cut.rfind(" ")] if " " in cut else cut
        text = cut.strip()

    padded = False
    for sentence in NEUTRAL_SENTENCES:
        if len(text) >= DESC_MIN:
            break
        text = f"{text}\n\n{sentence}".strip()
        padded = True
    return text, padded


def _fit_queries(
    queries: list[str], product: ProductInput
) -> tuple[list[str], list[str], list[str]]:
    """Отбрасывает запросы с неподтверждёнными данными и добивает до минимума."""
    source = product.fact_text()
    claim_source = product.source_text()
    result: list[str] = []
    removed_numbers: list[str] = []
    removed_claims: list[str] = []

    for query in queries:
        bad_numbers = find_unsupported_numbers(query, source, name=product.name)
        bad_claims = find_unsupported_claims(query, claim_source)
        if bad_numbers or bad_claims:
            removed_numbers.extend(n for n in bad_numbers if n not in removed_numbers)
            removed_claims.extend(c for c in bad_claims if c not in removed_claims)
            continue
        if query not in result:
            result.append(query)

    fallback = (
        f"{product.name} купить",
        f"{product.name} характеристики",
        f"{product.name} цена",
    )
    for candidate in fallback:
        if len(result) >= QUERIES_MIN:
            break
        if candidate not in result:
            result.append(candidate)

    return result[:QUERIES_MAX], removed_numbers, removed_claims


def _finalize(
    card: ProductCard,
    product: ProductInput,
    missing_titles: list[str],
    builder_warnings: list[str],
) -> None:
    """Детерминированная доводка карточки после ответа модели."""
    source = product.fact_text()
    claim_source = product.source_text()
    warnings: list[str] = list(builder_warnings)

    removed_numbers: list[str] = []
    removed_claims: list[str] = []

    title, numbers, claims = sanitize_unsupported(
        card.seo_title, source, name=product.name, claims_source=claim_source
    )
    removed_numbers.extend(numbers)
    removed_claims.extend(claims)
    card.seo_title = _truncate_title(title, product.name)

    description, numbers, claims = sanitize_unsupported(
        card.seo_description, source, name=product.name, claims_source=claim_source
    )
    removed_numbers.extend(n for n in numbers if n not in removed_numbers)
    removed_claims.extend(c for c in claims if c not in removed_claims)
    card.seo_description, padded = _fit_description(description)

    commercial, numbers, claims = sanitize_unsupported(
        card.commercial_block, source, name=product.name, claims_source=claim_source
    )
    removed_numbers.extend(n for n in numbers if n not in removed_numbers)
    removed_claims.extend(c for c in claims if c not in removed_claims)
    card.commercial_block = commercial.strip()

    card.search_queries, numbers, claims = _fit_queries(card.search_queries, product)
    removed_numbers.extend(n for n in numbers if n not in removed_numbers)
    removed_claims.extend(c for c in claims if c not in removed_claims)

    if removed_numbers:
        warnings.append(
            "Удалены значения, которых нет во входных данных: " + ", ".join(removed_numbers)
        )
    if removed_claims:
        warnings.append(
            "Удалены утверждения, которых нет во входных данных: "
            + ", ".join(removed_claims)
        )
    if padded:
        warnings.append(
            "Описание дополнено нейтральными фразами без технических фактов "
            "до нижней границы объёма."
        )

    remaining = collect_problems(
        card, source, name=product.name, claim_source=claim_source
    )
    if remaining:
        warnings.append("Не удалось исправить автоматически: " + "; ".join(remaining))

    if len(card.seo_description) < DESC_MIN:
        warnings.append(
            f"Описание короче {DESC_MIN} символов ({len(card.seo_description)}) — "
            "во входных данных слишком мало фактов."
        )
    if len(card.commercial_block) < COMMERCIAL_MIN:
        warnings.append("Коммерческий блок получился коротким — проверьте вручную.")

    card.missing_data = missing_titles
    card.warnings = warnings


# --------------------------------------------------------------------------
# Генерация
# --------------------------------------------------------------------------


async def generate_card(
    product: ProductInput,
    client: LLMClient,
    *,
    provider_title: str = "",
    max_repairs: int = 1,
) -> GenerationResult:
    started = time.perf_counter()
    profile = detect_profile(product.name, product.description)
    missing_keys = find_missing_fields(product.specs, profile, product.description)
    titles = {field.key: field.title for field in profile.fields}
    missing_titles = [titles.get(key, key) for key in missing_keys]

    request = build_request(product, profile.title, missing_titles)
    source = product.fact_text()
    claim_source = product.source_text()

    raw = await client.complete_json(request)
    data = parse_json_object(raw)
    card, builder_warnings = _normalize_card(data, product, profile.id)
    problems = collect_problems(card, source, name=product.name, claim_source=claim_source)
    attempts = 1
    repair_notes: list[str] = []

    while problems and attempts <= max_repairs:
        try:
            raw = await client.complete_json(build_repair_request(request, problems))
            data = parse_json_object(raw)
            repaired_card, repaired_warnings = _normalize_card(data, product, profile.id)
            repaired_problems = collect_problems(
                repaired_card, source, name=product.name, claim_source=claim_source
            )
        except LLMError as exc:
            # Первая версия карточки уже валидна по структуре — не теряем её
            # из-за сбоя повторного запроса.
            repair_notes.append(f"Повторный запрос не удался, оставлена первая версия: {exc}")
            break
        card, builder_warnings, problems = repaired_card, repaired_warnings, repaired_problems
        attempts += 1

    _finalize(card, product, missing_titles, builder_warnings)
    if repair_notes:
        card.warnings = repair_notes + card.warnings

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    render_options = {
        "provider": provider_title or getattr(client, "name", ""),
        "model": getattr(client, "model", ""),
        "family_title": profile.title,
        "elapsed_ms": elapsed_ms,
        "attempts": attempts,
    }
    markdown = to_markdown(card, product, **render_options)
    text = to_plain_text(card, product, **render_options)

    return GenerationResult(
        card=card,
        markdown=markdown,
        text=text,
        provider=provider_title or getattr(client, "name", ""),
        model=getattr(client, "model", ""),
        attempts=attempts,
        elapsed_ms=elapsed_ms,
        family_title=profile.title,
    )
