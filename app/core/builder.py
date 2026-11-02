"""Сборка списка характеристик.

Модель решает, какие характеристики важны и как их назвать, но значение
всегда берётся из входных данных дословно. Если значения нет — пишем
«данных нет». Так исключается самый частый вид выдумывания: правдоподобный,
но неверный параметр.
"""

from __future__ import annotations

from app.core.profiles import get_profile
from app.core.text import normalize
from app.models import MISSING_VALUE, SpecItem


def _match_spec_key(source_key: str, specs: dict[str, str]) -> str | None:
    wanted = normalize(source_key)
    if not wanted:
        return None
    for key in specs:
        if normalize(key) == wanted:
            return key
    for key in specs:
        key_norm = normalize(key)
        if wanted in key_norm or key_norm in wanted:
            return key
    return None


def build_characteristics(
    llm_items: list[dict],
    specs: dict[str, str],
    profile_id: str,
) -> tuple[list[SpecItem], list[str]]:
    """Собирает характеристики карточки из ответа модели и входных данных."""
    items: list[SpecItem] = []
    warnings: list[str] = []
    used_names: set[str] = set()

    for raw in llm_items or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("source_key") or "").strip()
        if not name:
            continue
        name_norm = normalize(name)
        if name_norm in used_names:
            continue
        used_names.add(name_norm)

        source_key = str(raw.get("source_key") or raw.get("key") or name).strip()
        matched = _match_spec_key(source_key, specs) or _match_spec_key(name, specs)
        if matched is None:
            items.append(SpecItem(name=name, value=MISSING_VALUE))
            warnings.append(f"Характеристика «{name}» отсутствует во входных данных")
            continue

        value = specs[matched].strip()
        if not value:
            items.append(SpecItem(name=name, value=MISSING_VALUE))
            warnings.append(f"Характеристика «{name}» отсутствует во входных данных")
            continue

        items.append(SpecItem(name=name, value=value))

    profile = get_profile(profile_id)
    for field in profile.fields:
        if not field.required:
            continue
        covered = any(
            any(normalize(synonym) in normalize(item.name) for synonym in field.synonyms)
            for item in items
        )
        if covered:
            continue

        spec_key = None
        for key in specs:
            key_norm = normalize(key)
            if any(normalize(synonym) in key_norm for synonym in field.synonyms):
                spec_key = key
                break
        value = specs[spec_key].strip() if spec_key else ""
        items.append(SpecItem(name=field.title, value=value or MISSING_VALUE))
        if not value:
            warnings.append(f"Характеристика «{field.title}» отсутствует во входных данных")

    return items, warnings
