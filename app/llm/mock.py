"""Офлайн-провайдер: детерминированная карточка по шаблону.

Нужен для двух вещей: показать результат без ключа и прогнать сквозные
тесты без сети. Ничего не выдумывает — собирает карточку только из
входных данных, поэтому ведёт себя как «идеальная» модель.
"""

from __future__ import annotations

import json

from app.core.text import split_sentences
from app.llm.base import LLMRequest

#: Фразы-заполнители без единого числа — чтобы не нарушать правило
#: «никаких данных, которых нет во входе».
FILLER = (
    "Прибор поставляется с документацией производителя.",
    "Характеристики в карточке приведены по данным поставщика.",
    "Перед заказом уточните исполнение и комплектацию у менеджера.",
    "Для подбора аналога и проверки совместимости обратитесь к инженерам компании.",
    "Карточка составлена только по переданным входным данным.",
    "Информация в карточке ограничена сведениями, переданными поставщиком.",
    "Дополнительные параметры следует уточнить у поставщика перед оформлением заказа.",
    "При подборе прибора учитывайте условия эксплуатации на объекте.",
    "Для получения консультации обратитесь в отдел продаж.",
    "Комплект поставки и исполнение уточняются при заказе.",
)

MIN_DESCRIPTION = 900


def build_mock_card(product: dict) -> dict:
    name = str(product.get("name") or "Товар").strip()
    manufacturer = str(product.get("manufacturer") or "").strip()
    specs: dict[str, str] = product.get("specs") or {}
    description = str(product.get("description") or "").strip()

    intro = f"{name}"
    if manufacturer:
        intro += f" от производителя {manufacturer}"
    intro += " — прибор для промышленных задач измерения и контроля."

    parts = [intro]
    parts.extend(split_sentences(description)[:3])
    if specs:
        parts.append("В карточке приведены характеристики: " + ", ".join(list(specs)[:6]) + ".")
    parts.append(
        "Описание составлено по данным поставщика, без домысливания параметров: "
        "если характеристика не указана во входных данных, в карточке стоит пометка."
    )

    # Заполнители не повторяем: повторяющийся текст выглядит как ошибка.
    for sentence in FILLER:
        if len(" ".join(parts)) >= MIN_DESCRIPTION:
            break
        parts.append(sentence)

    queries = [
        f"{name} купить",
        f"{name} характеристики",
        f"{name} описание",
    ]
    if manufacturer:
        queries.append(f"{manufacturer} {name}")

    return {
        "seo_title": name,
        "seo_description": " ".join(parts),
        "characteristics": [{"name": key, "source_key": key} for key in specs],
        "search_queries": queries,
        "commercial_block": (
            f"{name} подходит для задач контроля и измерений на производстве, "
            "в инженерных системах и на объектах промышленной автоматизации. "
            "Помогает проверить совместимость с существующим оборудованием, "
            "подобрать аналог и оформить заявку у менеджера."
        ),
    }


class MockClient:
    def __init__(self, *, model: str = "mock", **_ignored: object) -> None:
        self.name = "Офлайн-демо"
        self.model = model or "mock"

    async def complete_json(self, request: LLMRequest) -> str:
        card = build_mock_card(request.payload.get("product", {}))
        return json.dumps(card, ensure_ascii=False)
