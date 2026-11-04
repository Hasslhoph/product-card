"""Представление карточки: Markdown для копирования и JSON для выгрузки."""

from __future__ import annotations

import json

from app.models import MISSING_VALUE, ProductCard, ProductInput

TITLE_LIMIT = 70
DESC_MIN = 800
DESC_MAX = 1200


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _inline(value: str) -> str:
    """Заголовки и подписи — в одну строку, без управляющих символов."""
    return " ".join(str(value).split())


def to_markdown(
    card: ProductCard,
    product: ProductInput,
    *,
    provider: str = "",
    model: str = "",
    family_title: str = "",
    elapsed_ms: int = 0,
    attempts: int = 0,
) -> str:
    """Готовый к копированию текст карточки для контент-менеджера."""
    lines: list[str] = []
    lines.append(f"# {_inline(product.name)}")
    lines.append("")
    lines.append(f"**Производитель:** {_inline(product.manufacturer) or MISSING_VALUE}")
    if family_title:
        lines.append(f"**Семейство:** {_inline(family_title)}")
    lines.append("")

    lines.append(f"## SEO-заголовок · {len(card.seo_title)}/{TITLE_LIMIT}")
    lines.append("")
    lines.append(card.seo_title)
    lines.append("")

    lines.append(f"## SEO-описание · {len(card.seo_description)} символов ({DESC_MIN}–{DESC_MAX})")
    lines.append("")
    lines.append(card.seo_description)
    lines.append("")

    lines.append("## Ключевые характеристики")
    lines.append("")
    lines.append("| Характеристика | Значение |")
    lines.append("| --- | --- |")
    for item in card.characteristics:
        lines.append(f"| {_escape(item.name)} | {_escape(item.value)} |")
    if not card.characteristics:
        lines.append(f"| — | {MISSING_VALUE} |")
    lines.append("")

    lines.append("## Поисковые запросы")
    lines.append("")
    for index, query in enumerate(card.search_queries, start=1):
        lines.append(f"{index}. {query}")
    lines.append("")

    lines.append("## Коммерческий блок для менеджера")
    lines.append("")
    lines.append(card.commercial_block or MISSING_VALUE)
    lines.append("")

    lines.append("## Требует уточнения")
    lines.append("")
    if card.missing_data:
        for field in card.missing_data:
            lines.append(f"- {field} — {MISSING_VALUE}")
    else:
        lines.append("- нет, данных достаточно для полной карточки")
    lines.append("")

    if card.warnings:
        lines.append("## Технические замечания проверок")
        lines.append("")
        for warning in card.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    footer = []
    if provider:
        footer.append(f"провайдер: {provider}")
    if model:
        footer.append(f"модель: {model}")
    if attempts:
        footer.append(f"обращений к модели: {attempts}")
    if elapsed_ms:
        footer.append(f"время: {elapsed_ms / 1000:.1f} с")
    if footer:
        lines.append("---")
        lines.append("_" + " · ".join(footer) + "_")

    return "\n".join(lines).strip() + "\n"


def _footer(provider: str, model: str, elapsed_ms: int, attempts: int) -> str:
    parts = []
    if provider:
        parts.append(f"провайдер: {provider}")
    if model:
        parts.append(f"модель: {model}")
    if attempts:
        parts.append(f"обращений к модели: {attempts}")
    if elapsed_ms:
        parts.append(f"время: {elapsed_ms / 1000:.1f} с")
    return " · ".join(parts)


def to_plain_text(
    card: ProductCard,
    product: ProductInput,
    *,
    provider: str = "",
    model: str = "",
    family_title: str = "",
    elapsed_ms: int = 0,
    attempts: int = 0,
) -> str:
    """Тот же контент без разметки — для вставки в поля CMS."""
    lines: list[str] = []
    lines.append(f"Карточка товара: {_inline(product.name)}")
    lines.append(f"Производитель: {_inline(product.manufacturer) or MISSING_VALUE}")
    if family_title:
        lines.append(f"Семейство: {_inline(family_title)}")
    lines.append("")

    lines.append(f"SEO-ЗАГОЛОВОК ({len(card.seo_title)}/{TITLE_LIMIT})")
    lines.append(card.seo_title)
    lines.append("")

    lines.append(
        f"SEO-ОПИСАНИЕ ({len(card.seo_description)} символов, {DESC_MIN}-{DESC_MAX})"
    )
    lines.append(card.seo_description)
    lines.append("")

    lines.append("КЛЮЧЕВЫЕ ХАРАКТЕРИСТИКИ")
    if card.characteristics:
        for item in card.characteristics:
            lines.append(f"{_inline(item.name)}: {_inline(item.value)}")
    else:
        lines.append(f"Характеристики: {MISSING_VALUE}")
    lines.append("")

    lines.append("ПОИСКОВЫЕ ЗАПРОСЫ")
    if card.search_queries:
        for index, query in enumerate(card.search_queries, start=1):
            lines.append(f"{index}. {query}")
    else:
        lines.append(MISSING_VALUE)
    lines.append("")

    lines.append("КОММЕРЧЕСКИЙ БЛОК ДЛЯ МЕНЕДЖЕРА")
    lines.append(card.commercial_block or MISSING_VALUE)
    lines.append("")

    lines.append("ТРЕБУЕТ УТОЧНЕНИЯ")
    if card.missing_data:
        for field in card.missing_data:
            lines.append(f"- {_inline(field)} — {MISSING_VALUE}")
    else:
        lines.append("- нет, данных достаточно для полной карточки")
    lines.append("")

    if card.warnings:
        lines.append("ТЕХНИЧЕСКИЕ ЗАМЕЧАНИЯ ПРОВЕРОК")
        for warning in card.warnings:
            lines.append(f"- {_inline(warning)}")
        lines.append("")

    footer = _footer(provider, model, elapsed_ms, attempts)
    if footer:
        lines.append(footer)

    return "\n".join(lines).strip() + "\n"


def to_json(card: ProductCard) -> str:
    return json.dumps(card.model_dump(), ensure_ascii=False, indent=2)
