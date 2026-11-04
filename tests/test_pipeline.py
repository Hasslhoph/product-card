"""Сквозные проверки конвейера без сети: mock-провайдер и сценарные клиенты."""

from __future__ import annotations

import asyncio
import copy

from app.core.guard import find_unsupported_numbers
from app.llm.base import LLMRequest
from app.llm.mock import MockClient, build_mock_card
from app.models import MISSING_VALUE, ProductInput
from app.pipeline import analyze_product, generate_card
from app.render import DESC_MAX, DESC_MIN, TITLE_LIMIT

PRODUCT_JSON = {
    "name": "Манометр показывающий МП3-У",
    "manufacturer": "Манотомь",
    "specs": {
        "Диапазон измерений": "0–1,6 МПа",
        "Класс точности": "1,5",
        "Диаметр корпуса": "100 мм",
        "Присоединение": "G1/2",
        "Степень защиты": "IP40",
    },
    "description": "Прибор предназначен для измерения избыточного давления неагрессивных жидкостей, пара и газа.",
}


def _product() -> ProductInput:
    return ProductInput.model_validate(copy.deepcopy(PRODUCT_JSON))


class ScriptedClient:
    """Клиент, который отдаёт заранее заданные ответы по очереди."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[LLMRequest] = []
        self.name = "Scripted"
        self.model = "scripted-1"

    async def complete_json(self, request: LLMRequest) -> str:
        self.requests.append(request)
        return self.responses.pop(0)


def test_full_card_meets_contract_with_mock_provider() -> None:
    result = asyncio.run(generate_card(_product(), MockClient()))

    card = result.card
    assert 0 < len(card.seo_title) <= TITLE_LIMIT
    assert DESC_MIN <= len(card.seo_description) <= DESC_MAX
    assert 3 <= len(card.search_queries) <= 5
    assert card.characteristics
    assert all(item.value for item in card.characteristics)
    assert card.commercial_block


def test_mock_provider_does_not_invent_numbers() -> None:
    product = _product()
    result = asyncio.run(generate_card(product, MockClient()))

    text = " ".join(
        [result.card.seo_title, result.card.seo_description, result.card.commercial_block]
    )
    assert find_unsupported_numbers(text, product.fact_text(), name=product.name) == []


def test_characteristics_are_taken_from_input_verbatim() -> None:
    result = asyncio.run(generate_card(_product(), MockClient()))

    values = {item.name: item.value for item in result.card.characteristics}
    assert values["Диапазон измерений"] == "0–1,6 МПа"
    assert values["Класс точности"] == "1,5"


def test_missing_required_field_is_marked() -> None:
    product = ProductInput.model_validate(
        {"name": "Манометр", "specs": {"Диаметр корпуса": "100 мм"}, "description": "Прибор."}
    )

    result = asyncio.run(generate_card(product, MockClient()))

    assert "Диапазон измерений" in result.card.missing_data
    range_item = next(
        (item for item in result.card.characteristics if item.name == "Диапазон измерений"), None
    )
    assert range_item is not None
    assert range_item.value == MISSING_VALUE


def test_answer_failing_requirements_triggers_one_repair() -> None:
    long_title_card = build_mock_card(PRODUCT_JSON)
    long_title_card["seo_title"] = "Очень длинный заголовок " * 5
    good_card = build_mock_card(PRODUCT_JSON)

    import json

    client = ScriptedClient(
        [
            json.dumps(long_title_card, ensure_ascii=False),
            json.dumps(good_card, ensure_ascii=False),
        ]
    )

    result = asyncio.run(generate_card(_product(), client))

    assert len(client.requests) == 2
    assert result.attempts == 2
    assert "ПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРОШЁЛ ПРОВЕРКУ" in client.requests[1].user
    assert len(result.card.seo_title) <= TITLE_LIMIT


def test_invented_number_is_removed_from_card() -> None:
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["seo_description"] = card["seo_description"] + " Прибор работает при 250 °C."
    client = ScriptedClient(
        [json.dumps(card, ensure_ascii=False), json.dumps(card, ensure_ascii=False)]
    )

    result = asyncio.run(generate_card(_product(), client))

    assert "250" not in result.card.seo_description
    assert any("250" in warning for warning in result.card.warnings)


def test_characteristic_unknown_to_input_is_marked_as_missing() -> None:
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["characteristics"].append({"name": "Интерфейс", "source_key": "интерфейс"})
    client = ScriptedClient([json.dumps(card, ensure_ascii=False)])

    result = asyncio.run(generate_card(_product(), client, max_repairs=0))

    interface = next(item for item in result.card.characteristics if item.name == "Интерфейс")
    assert interface.value == MISSING_VALUE


def test_renamed_characteristic_keeps_value_from_source_key() -> None:
    """Модель может переименовать подпись — значение всё равно берётся по source_key."""
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["characteristics"] = [{"name": "Присоединительная резьба", "source_key": "Присоединение"}]
    client = ScriptedClient([json.dumps(card, ensure_ascii=False)])

    result = asyncio.run(generate_card(_product(), client, max_repairs=0))

    item = result.card.characteristics[0]
    assert item.name == "Присоединительная резьба"
    assert item.value == "G1/2"


def test_empty_title_falls_back_to_product_name() -> None:
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["seo_title"] = ""
    client = ScriptedClient([json.dumps(card, ensure_ascii=False)])

    result = asyncio.run(generate_card(_product(), client, max_repairs=0))

    assert result.card.seo_title
    assert "МП3-У" in result.card.seo_title


def test_invented_accuracy_using_model_digit_is_removed() -> None:
    """«Класс точности 3» не проходит только потому, что в названии есть МП3-У."""
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["seo_description"] = (
        "Манометр МП3-У для измерения давления.\n\n"
        "Класс точности 3.\n\n"
        "Диаметр корпуса 100 мм.\n\n"
        "Прибор подходит для промышленных задач контроля давления."
    )
    client = ScriptedClient([json.dumps(card, ensure_ascii=False)])

    result = asyncio.run(generate_card(_product(), client, max_repairs=0))

    assert "класс точности 3" not in result.card.seo_description.lower()
    assert any("3" in warning for warning in result.card.warnings)


def test_query_with_invented_number_is_dropped() -> None:
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["search_queries"] = [
        "манометр мп3-у на 250 бар купить",
        "манометр мп3-у цена",
        "манометр мп3-у характеристики",
    ]
    client = ScriptedClient([json.dumps(card, ensure_ascii=False)])

    result = asyncio.run(generate_card(_product(), client, max_repairs=0))

    assert all("250" not in query for query in result.card.search_queries)
    assert any("250" in warning for warning in result.card.warnings)


def test_unsupported_claim_is_removed_from_description() -> None:
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["seo_description"] += " Корпус выполнен из нержавеющей стали."
    client = ScriptedClient(
        [json.dumps(card, ensure_ascii=False), json.dumps(card, ensure_ascii=False)]
    )

    result = asyncio.run(generate_card(_product(), client))

    assert "нержавеющ" not in result.card.seo_description.lower()
    assert any("нержавеющ" in warning for warning in result.card.warnings)


def test_wrong_label_is_replaced_by_real_key() -> None:
    """Значение не должно попадать под чужую подпись."""
    import json

    card = build_mock_card(PRODUCT_JSON)
    card["characteristics"] = [{"name": "Гарантия", "source_key": "Диаметр корпуса"}]
    client = ScriptedClient([json.dumps(card, ensure_ascii=False)])

    result = asyncio.run(generate_card(_product(), client, max_repairs=0))

    labels = {item.name for item in result.card.characteristics}
    assert "Гарантия" not in labels
    diameter = next(item for item in result.card.characteristics if item.name == "Диаметр корпуса")
    assert diameter.value == "100 мм"


def test_failed_repair_keeps_first_valid_card() -> None:
    import json

    good_card = build_mock_card(PRODUCT_JSON)
    good_card["seo_title"] = "Очень длинный заголовок " * 5
    client = ScriptedClient(
        [json.dumps(good_card, ensure_ascii=False), "Извините, не могу ответить"]
    )

    result = asyncio.run(generate_card(_product(), client))

    assert result.card.seo_title
    assert any("Повторный запрос не удался" in warning for warning in result.card.warnings)


def test_claim_from_product_name_is_not_removed() -> None:
    """Название товара — тоже факт: «взрывозащищённый» в названии не выдумка."""
    import json

    product_json = {
        "name": "Сигнализатор взрывозащищённый СТГ-1",
        "manufacturer": "Аналитприбор",
        "specs": {"Определяемый газ": "метан"},
        "description": "Прибор для контроля загазованности помещений.",
    }
    product = ProductInput.model_validate(product_json)
    client = ScriptedClient([json.dumps(build_mock_card(product_json), ensure_ascii=False)])

    result = asyncio.run(generate_card(product, client, max_repairs=0))

    assert "взрывозащищ" in result.card.seo_description.lower()


def test_claim_from_name_does_not_trigger_repair() -> None:
    """Слово из названия не должно считаться выдумкой и вызывать повторный запрос."""
    import json

    product_json = {
        "name": "Сигнализатор взрывозащищённый СТГ-1",
        "manufacturer": "Аналитприбор",
        "specs": {"Определяемый газ": "метан"},
        "description": "Прибор для контроля загазованности помещений.",
    }
    product = ProductInput.model_validate(product_json)
    client = ScriptedClient([json.dumps(build_mock_card(product_json), ensure_ascii=False)])

    result = asyncio.run(generate_card(product, client))

    assert len(client.requests) == 1
    assert result.attempts == 1
    # Предупреждения об отсутствующих характеристиках — норма, а вот
    # «удалено утверждение взрывозащ» быть не должно.
    assert not any("взрывозащ" in warning.lower() for warning in result.card.warnings)


def test_field_described_in_text_is_not_reported_missing() -> None:
    product = ProductInput.model_validate(
        {
            "name": "Манометр",
            "specs": {},
            "description": "Прибор для измерения давления воды, пара и газа.",
        }
    )

    report = analyze_product(product)

    assert "Измеряемая среда" not in report.missing


def test_analysis_reports_missing_fields_without_llm_call() -> None:
    report = analyze_product(_product())

    assert report.family == "manometer"
    assert "Диапазон измерений" in report.present
    # Измеряемая среда упомянута в описании техпаспорта — значит она не «отсутствует».
    assert "Измеряемая среда" in report.present
    assert "Материал корпуса" in report.missing
    assert report.can_generate is True


def test_analysis_handles_completely_empty_product() -> None:
    product = ProductInput.model_validate({"name": "Неизвестный прибор"})

    report = analyze_product(product)

    assert report.family == "generic"
    assert report.missing
    assert any("характеристики" in warning.lower() for warning in report.warnings)
