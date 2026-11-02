"""Нормализация текста и поиск чисел — общая база для всех проверок."""

from __future__ import annotations

import re

# Разные виды тире приводим к дефису, чтобы «0–1,6» и «0-1,6» считались одним.
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_DASH_RE = re.compile(f"[{_DASHES}]")
_SPACES_RE = re.compile(r"\s+")

#: Число, дробь или диапазон через косую черту: 1,5 · 100 · 1/2 · 0.7
NUMBER_RE = re.compile(r"\d+(?:[./]\d+)*")

#: Предложение вместе со знаком конца, чтобы не терять пунктуацию при склейке.
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")


def normalize(value: str) -> str:
    """Приводит текст к виду, пригодному для сравнения."""
    text = value.lower().replace("ё", "е")
    text = _DASH_RE.sub("-", text)
    text = text.replace(",", ".")
    text = _SPACES_RE.sub(" ", text)
    return text.strip()


def extract_numbers(text: str) -> list[str]:
    """Возвращает числа в порядке появления, без повторов."""
    seen: list[str] = []
    for token in NUMBER_RE.findall(normalize(text)):
        if token not in seen:
            seen.append(token)
    return seen


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_RE.findall(text) if part.strip()]
