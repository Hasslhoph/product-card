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
    ("взрывоопас", "взрывозащита"),
    ("atex", "взрывозащита"),
    ("iecex", "взрывозащита"),
    ("exi", "взрывозащита"),
    ("exd", "взрывозащита"),
    ("ex d", "взрывозащита"),
    ("оригинальн", "происхождение"),
)

#: Точка считается концом предложения, только если рядом нет цифр —
#: иначе «0.7 кг» и «1.5» разрезаются на части.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<!\d)([.!?])(?!\d)\s*")
_NAME_TOKEN_RE = re.compile(r"[a-zа-я0-9][a-zа-я0-9\-]*")


def _split_sentences(text: str) -> list[str]:
    marked = _SENTENCE_BOUNDARY_RE.sub(r"\1\n", text)
    return [part.strip() for part in marked.split("\n") if part.strip()]


def _name_anchors(name: str, number: str) -> list[str]:
    """Токены названия, в которых встречается число (например «мп3-у» для «3»)."""
    if not name or not number:
        return []
    anchors: list[str] = []
    for token in _NAME_TOKEN_RE.findall(normalize(name)):
        if number in extract_numbers(token) and token not in anchors:
            anchors.append(token)
    return anchors


def find_unsupported_numbers(text: str, source: str, *, name: str = "") -> list[str]:
    """Возвращает числа из текста, которых нет в источнике.

    Сравнение идёт по целым числовым токенам: «40» из «IP40» и «1/2» из
    «G1/2» считаются подтверждёнными, а «10» больше не подтверждается
    числом «100».

    Числа из названия товара (артикул МП3-У → «3») подтверждают текст
    только если в проверяемой фразе есть сам токен артикула — иначе
    выдуманное «класс точности 3» не пройдёт проверку.
    """
    source_tokens = set(extract_numbers(source))
    text_norm = normalize(text)
    unsupported: list[str] = []
    for number in extract_numbers(text):
        if number in source_tokens or number in unsupported:
            continue
        anchors = _name_anchors(name, number)
        if anchors and any(anchor in text_norm for anchor in anchors):
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


def sanitize_unsupported(
    text: str,
    source: str,
    *,
    name: str = "",
    claims_source: str | None = None,
) -> tuple[str, list[str], list[str]]:
    """Удаляет предложения с неподтверждёнными числами и утверждениями.

    Возвращает очищенный текст, список удалённых чисел и список удалённых
    утверждений. Пустые строки между абзацами сохраняются, чтобы описание
    не превращалось в один сплошной блок.

    Источники разные: числа сверяются с ``source`` (без названия, чтобы цифры
    артикула не подтверждали любые значения), а утверждения — с
    ``claims_source`` по умолчанию, куда название входит: название товара само
    является фактом.
    """
    claim_source = claims_source if claims_source is not None else source
    removed_numbers: list[str] = []
    removed_claims: list[str] = []
    paragraphs: list[str] = []

    # Абзацы разделены пустой строкой — её нельзя терять, иначе описание
    # превращается в один сплошной блок и не проходит требование «3–4 абзаца».
    for block in re.split(r"\n\s*\n", text):
        kept: list[str] = []
        for sentence in _split_sentences(block.replace("\n", " ")):
            bad_numbers = find_unsupported_numbers(sentence, source, name=name)
            bad_claims = find_unsupported_claims(sentence, claim_source)
            if bad_numbers or bad_claims:
                removed_numbers.extend(n for n in bad_numbers if n not in removed_numbers)
                removed_claims.extend(c for c in bad_claims if c not in removed_claims)
                continue
            kept.append(sentence)
        if kept:
            paragraphs.append(" ".join(kept))

    cleaned = "\n\n".join(paragraphs)
    return cleaned.strip(), removed_numbers, removed_claims
