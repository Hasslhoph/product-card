"""Страж чисел: главная защита от выдуманных технических значений.

Идея в том, что промпт — просьба, а не гарантия. Поэтому все числа из
ответа модели сверяются с тем, что реально было на входе. Число, которого
нет во входных данных, не попадает в готовую карточку.
"""

from __future__ import annotations

from app.core.text import extract_numbers, normalize, split_sentences


def find_unsupported_numbers(text: str, source: str) -> list[str]:
    """Возвращает числа из текста, которых нет в источнике.

    Сравнение подстрочное: «40» из «IP40» и «1/2» из «G1/2» считаются
    подтверждёнными. Так мы избегаем ложных срабатываний, но при этом
    любое действительно новое число ловится.
    """
    source_norm = normalize(source)
    unsupported: list[str] = []
    for number in extract_numbers(text):
        if number in source_norm or number in unsupported:
            continue
        unsupported.append(number)
    return unsupported


def sanitize_unsupported(text: str, source: str) -> tuple[str, list[str]]:
    """Удаляет предложения с неподтверждёнными числами.

    Возвращает очищенный текст и список удалённых чисел, чтобы вызывающий
    код мог показать предупреждение контент-менеджеру.
    """
    kept: list[str] = []
    removed: list[str] = []
    for sentence in split_sentences(text):
        unsupported = find_unsupported_numbers(sentence, source)
        if unsupported:
            removed.extend(number for number in unsupported if number not in removed)
            continue
        kept.append(sentence)
    return " ".join(kept).strip(), removed
