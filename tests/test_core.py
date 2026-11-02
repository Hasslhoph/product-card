"""Тесты детерминированного ядра: разбор входа, профили, защита от выдуманных данных.

Сеть здесь не используется вообще — это принципиально: ядро, которое
гарантирует отсутствие выдуманных характеристик, должно быть проверяемым
без обращения к LLM.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.builder import build_characteristics
from app.core.guard import find_unsupported_numbers, sanitize_unsupported
from app.core.profiles import detect_profile, find_missing_fields
from app.models import ProductInput


# --------------------------------------------------------------------------
# Разбор входных данных
# --------------------------------------------------------------------------


def test_specs_can_be_list_of_pairs() -> None:
    """Контент-менеджеры присылают характеристики и списком, не только словарём."""
    product = ProductInput.model_validate(
        {
            "name": "Манометр МП3-У",
            "manufacturer": "Манотомь",
            "specs": [
                {"name": "Диаметр корпуса", "value": "100 мм"},
                {"name": "Класс точности", "value": "1,5"},
            ],
            "description": "Прибор для измерения избыточного давления.",
        }
    )

    assert product.specs == {"Диаметр корпуса": "100 мм", "Класс точности": "1,5"}


def test_empty_spec_values_are_dropped() -> None:
    product = ProductInput.model_validate(
        {
            "name": "Манометр",
            "specs": {"Класс точности": "  ", "Диаметр корпуса": None, "Масса": "0,7 кг"},
        }
    )

    assert product.specs == {"Масса": "0,7 кг"}
    assert "Класс точности" not in product.specs


def test_numbers_are_converted_to_text() -> None:
    product = ProductInput.model_validate({"name": "Манометр", "specs": {"Диаметр": 100}})

    assert product.specs["Диаметр"] == "100"


def test_empty_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProductInput.model_validate({"name": "   ", "specs": {}})


def test_text_fields_are_trimmed() -> None:
    product = ProductInput.model_validate(
        {"name": "  Манометр МП3-У  ", "manufacturer": " Манотомь ", "description": "  Описание.  "}
    )

    assert product.name == "Манометр МП3-У"
    assert product.manufacturer == "Манотомь"
    assert product.description == "Описание."


# --------------------------------------------------------------------------
# Профили полноты данных
# --------------------------------------------------------------------------


def test_manometer_profile_is_detected() -> None:
    profile = detect_profile("МП3-У — манометр показывающий", "")

    assert profile.id == "manometer"
    assert "диапазон измерений" in {field.key for field in profile.fields}


def test_thermometer_profile_is_detected() -> None:
    profile = detect_profile("ОВЕН ДТС035 — термопреобразователь сопротивления", "")

    assert profile.id == "thermometer"


def test_unknown_device_falls_back_to_generic_profile() -> None:
    profile = detect_profile("Устройство согласования", "")

    assert profile.id == "generic"


def test_missing_fields_are_reported_by_synonyms() -> None:
    specs = {"Диаметр корпуса (мм)": "100", "Резьба присоединительного штуцера": "G1/2"}
    missing = find_missing_fields(specs, detect_profile("манометр показывающий", ""))

    assert "присоединение" not in missing
    assert "диаметр корпуса" not in missing
    assert "диапазон измерений" in missing
    assert "класс точности" in missing


def test_missing_fields_are_reported_for_empty_specs() -> None:
    missing = find_missing_fields({}, detect_profile("манометр", ""))

    assert "диапазон измерений" in missing
    assert len(missing) >= 3


# --------------------------------------------------------------------------
# Защита от выдуманных значений
# --------------------------------------------------------------------------


def test_invented_number_is_detected() -> None:
    source = "Диапазон измерений: 0–1,6 МПа. Класс точности 1,5. Диаметр корпуса 100 мм."
    text = "Манометр с диапазоном 0–1,6 МПа и классом точности 2,5 для труб 1/2 дюйма."

    assert find_unsupported_numbers(text, source) == ["2.5", "1/2"]


def test_known_numbers_are_not_flagged() -> None:
    source = "Диапазон 0–1,6 МПа, корпус 100 мм, степень защиты IP40, резьба G1/2, 0,7 кг."
    text = "Работает в диапазоне 0–1,6 МПа, корпус 100 мм, IP40, присоединение G1/2, масса 0,7 кг."

    assert find_unsupported_numbers(text, source) == []


def test_number_formatting_differences_are_tolerated() -> None:
    source = "Класс точности 1.5, диапазон 1,6 МПа"
    text = "Класс точности 1,5 при диапазоне 1.6 МПа"

    assert find_unsupported_numbers(text, source) == []


def test_unsupported_sentences_are_removed() -> None:
    source = "Прибор для измерения давления. Диаметр корпуса 100 мм."
    text = "Прибор для измерения давления. Работает при 250 °C. Диаметр корпуса 100 мм."

    cleaned, removed = sanitize_unsupported(text, source)

    assert "250" not in cleaned
    assert "Диаметр корпуса 100 мм." in cleaned
    assert removed == ["250"]


# --------------------------------------------------------------------------
# Сборка характеристик
# --------------------------------------------------------------------------


def test_characteristics_use_values_from_input_verbatim() -> None:
    specs = {"Диаметр корпуса": "100 мм", "Класс точности": "1,5"}
    llm_items = [
        {"name": "Диаметр корпуса", "source_key": "Диаметр корпуса"},
        {"name": "Класс точности", "source_key": "класс точности"},
    ]

    items, warnings = build_characteristics(llm_items, specs, profile_id="generic")

    assert [item.value for item in items] == ["100 мм", "1,5"]
    assert warnings == []


def test_unknown_characteristic_is_marked_as_missing() -> None:
    specs = {"Диаметр корпуса": "100 мм"}
    llm_items = [
        {"name": "Диаметр корпуса", "source_key": "Диаметр корпуса"},
        {"name": "Интерфейс", "source_key": "интерфейс"},
    ]

    items, warnings = build_characteristics(llm_items, specs, profile_id="generic")

    interface = next(item for item in items if item.name == "Интерфейс")
    assert interface.value == "данных нет"
    assert any("интерфейс" in warning.lower() for warning in warnings)


def test_required_fields_are_appended_when_absent() -> None:
    specs = {"Диаметр корпуса": "100 мм"}

    items, _ = build_characteristics([], specs, profile_id="manometer")

    names = [item.name for item in items]
    assert "Диапазон измерений" in names
    missing_item = next(item for item in items if item.name == "Диапазон измерений")
    assert missing_item.value == "данных нет"


def test_values_cannot_be_overridden_by_model() -> None:
    """Модель назвала ключ, но значение всё равно берётся из входа."""
    specs = {"Класс точности": "1,5"}

    items, _ = build_characteristics(
        [{"name": "Класс точности", "source_key": "Класс точности", "value": "2,5"}],
        specs,
        profile_id="generic",
    )

    assert items[0].value == "1,5"
