"""Сборка списка характеристик.

Модель решает, какие характеристики важны, но значение всегда берётся из
входных данных дословно, а подпись проверяется: если модель назвала строку
не тем полем, подпись заменяется на реальный ключ. Так исключается самый
опасный вид ошибки — «правильное значение под неправильным названием».
"""

from __future__ import annotations

import re

from app.core.profiles import get_profile
from app.core.text import normalize
from app.models import MISSING_VALUE, SpecItem

#: Служебные слова и единицы не участвуют в сопоставлении ключей.
_STOPWORDS = frozenset(
    {
        "и",
        "в",
        "на",
        "для",
        "по",
        "с",
        "из",
        "или",
        "не",
        "мм",
        "см",
        "м",
        "кг",
        "г",
        "ма",
        "в",
        "шт",
        "тип",
        "типа",
    }
)

_WORD_RE = re.compile(r"[a-zа-я0-9]+")


def _tokens(value: str) -> set[str]:
    return {token for token in _WORD_RE.findall(normalize(value)) if token not in _STOPWORDS}


def _related(left: str, right: str) -> bool:
    """Сравнение слов с учётом словоформ: «присоединение» ≈ «присоединительная»."""
    size = min(len(left), len(right), 6)
    return size >= 4 and left[:size] == right[:size]


def _overlap(left: set[str], right: set[str]) -> int:
    return sum(1 for word in left if any(_related(word, other) for other in right))


def _match_spec_key(value: str, specs: dict[str, str]) -> str | None:
    """Ищет ключ характеристик: сначала точное совпадение, затем по словам."""
    wanted_norm = normalize(value)
    if not wanted_norm:
        return None
    for key in specs:
        if normalize(key) == wanted_norm:
            return key

    wanted = _tokens(value)
    if not wanted:
        return None
    best: str | None = None
    best_score = 0
    for key in specs:
        score = _overlap(wanted, _tokens(key))
        if score > best_score:
            best, best_score = key, score
    return best


def _label(name: str, source_key: str, matched_key: str) -> str:
    """Подпись строки должна соответствовать ключу, иначе берём сам ключ."""
    if not name:
        return matched_key
    name_tokens = _tokens(name)
    if _overlap(name_tokens, _tokens(source_key)) or _overlap(name_tokens, _tokens(matched_key)):
        return name
    return matched_key


def build_characteristics(
    llm_items: list[dict],
    specs: dict[str, str],
    profile_id: str,
) -> tuple[list[SpecItem], list[str]]:
    """Собирает характеристики карточки из ответа модели и входных данных."""
    items: list[SpecItem] = []
    warnings: list[str] = []
    used_keys: set[str] = set()

    for raw in llm_items or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("source_key") or "").strip()
        source_key = str(raw.get("source_key") or raw.get("key") or name).strip()
        if not name and not source_key:
            continue

        matched = _match_spec_key(source_key, specs) or _match_spec_key(name, specs)
        if matched is None:
            label = name or source_key
            if normalize(label) in {normalize(item.name) for item in items}:
                continue
            items.append(SpecItem(name=label, value=MISSING_VALUE))
            warnings.append(f"Характеристика «{label}» отсутствует во входных данных")
            continue

        if matched in used_keys:
            continue
        used_keys.add(matched)

        value = specs[matched].strip()
        if not value:
            items.append(SpecItem(name=_label(name, source_key, matched), value=MISSING_VALUE))
            warnings.append(f"Характеристика «{matched}» отсутствует во входных данных")
            continue

        items.append(SpecItem(name=_label(name, source_key, matched), value=value))

    profile = get_profile(profile_id)
    present_names = {normalize(item.name) for item in items}
    for field in profile.fields:
        if not field.required:
            continue
        covered = any(
            any(normalize(synonym) in name for synonym in field.synonyms)
            for name in present_names
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
        present_names.add(normalize(field.title))
        if not value:
            warnings.append(f"Характеристика «{field.title}» отсутствует во входных данных")

    return items, warnings
