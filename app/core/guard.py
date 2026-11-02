"""Страж данных: главная защита от выдуманных значений.

Промпт — просьба, а не гарантия. Поэтому готовый текст проверяется кодом
по двум словарям: числа (сравнение по целым токенам, а не по подстроке) и
типовые технические утверждения — материалы, стандарты, сертификаты,
гарантии, взрывозащита. Всё, чего нет во входных данных, из карточки
удаляется вместе с предложением.
"""

from __future__ import annotations

import re

from app.core.text import extract_numbers, normalize

#: Маркеры технических и маркетинговых утверждений, которые модель любит
#: «дописывать». Если маркера нет во входных данных — предложение удаляется.
CLAIM_MARKERS: tuple[tuple[str, str], ...] = (
    ("нержавеющ", "марка материала"),
    ("латун", "марка материала"),
    ("бронз", "марка материала"),
    ("алюмини", "марка материала"),
    ("титан", "марка материала"),
    ("поликарбонат", "марка материала"),
    ("тефлон", "марка материала"),
    ("гост", "стандарт"),
    ("din ", "стандарт"),
    ("iso ", "стандарт"),
    ("ansi", "стандарт"),
    ("сертифик", "сертификат"),
    ("госреестр", "сертификат"),
    ("деклараци", "сертификат"),
    ("свидетельств", "сертификат"),
    ("гаранти", "гарантия"),
    ("взрывозащ", "взрывозащита"),
    ("atex", "взрывозащита"),
    ("оригинальн", "происхождение"),
)

#: Точка считается концом предложения, только если рядом нет цифр —
#: иначе «0.7 кг» и «1.5» разрезаются на части.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<!\d)([.!?])(?!\d)\s*")


def _split_sentences(text: str) -> list[str]:
    marked = _SENTENCE_BOUNDARY_RE.sub(r"\1\n", text)
    return [part.strip() for part in marked.split("\n") if part.strip()]


def find_unsupported_numbers(text: str, source: str) -> list[str]:
    """Возвращает числа из текста, которых нет в источнике.

    Сравнение идёт по целым числовым токенам: «40» из «IP40» и «1/2» из
    «G1/2» считаются подтверждёнными, а «10» больше не подтверждается
    числом «100».
    """
    source_tokens = set(extract_numbers(source))
    unsupported: list[str] = []
    for number in extract_numbers(text):
        if number in source_tokens or number in unsupported:
            continue
        unsupported.append(number)
    return unsupported


def find_unsupported_claims(text: str, source: str) -> list[str]:
    """Возвращает технические утверждения, которых нет во входных данных."""
    text_norm = normalize(text)
    source_norm = normalize(source)
    found: list[str] = []
    for marker, _ in CLAIM_MARKERS:
        if marker in text_norm and marker not in source_norm:
            found.append(marker.strip())
    return found


def sanitize_unsupported(text: str, source: str) -> tuple[str, list[str], list[str]]:
    """Удаляет предложения с неподтверждёнными числами и утверждениями.

    Возвращает очищенный текст, список удалённых чисел и список удалённых
    утверждений. Пустые строки между абзацами сохраняются, чтобы описание
    не превращалось в один сплошной блок.
    """
    removed_numbers: list[str] = []
    removed_claims: list[str] = []
    paragraphs: list[str] = []

    for paragraph in text.split("\n"):
        kept: list[str] = []
        for sentence in _split_sentences(paragraph):
            bad_numbers = find_unsupported_numbers(sentence, source)
            bad_claims = find_unsupported_claims(sentence, source)
            if bad_numbers or bad_claims:
                removed_numbers.extend(n for n in bad_numbers if n not in removed_numbers)
                removed_claims.extend(c for c in bad_claims if c not in removed_claims)
                continue
            kept.append(sentence)
        if kept:
            paragraphs.append(" ".join(kept))

    cleaned = "\n".join(paragraphs)
    return cleaned.strip(), removed_numbers, removed_claims
