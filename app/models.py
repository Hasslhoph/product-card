"""Схемы данных: входной товар, карточка товара, отчёт о полноте."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Значение-заглушка для отсутствующих данных: если характеристики нет
#: во входных данных, модель не должна её придумывать.
MISSING_VALUE = "данных нет"


class ProductInput(BaseModel):
    """Товар на входе: название, производитель, характеристики, описание.

    Дополнительно принимает ``specs`` списком пар — так характеристики
    удобнее присылать из таблицы или парсера.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=300, description="Название товара")
    manufacturer: str = Field(default="", max_length=200, description="Производитель")
    specs: dict[str, str] = Field(
        default_factory=dict, description="Краткие характеристики: название → значение"
    )
    description: str = Field(
        default="", max_length=20000, description="Описание или фрагмент техпаспорта"
    )

    @field_validator("specs", mode="before")
    @classmethod
    def _coerce_specs(cls, value: Any) -> dict[str, str]:
        """Приводит характеристики к словарю строк и выбрасывает пустые значения."""
        if value is None:
            return {}
        if isinstance(value, dict):
            raw = list(value.items())
        elif isinstance(value, (list, tuple)):
            raw = []
            for item in value:
                if isinstance(item, dict):
                    key = item.get("name") or item.get("key") or item.get("title")
                    raw.append((key, item.get("value")))
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    raw.append((item[0], item[1]))
        else:
            raise ValueError("ожидается объект или список пар «название — значение»")

        result: dict[str, str] = {}
        for key, value in raw:
            if key is None or value is None:
                continue
            key_text = str(key).strip()
            value_text = str(value).strip()
            if not key_text or not value_text:
                continue
            # Ограничиваем объём: защита от случайной вставки целого
            # техпаспорта в характеристики и от раздувания стоимости запроса.
            if len(key_text) > 120 or len(value_text) > 2000:
                continue
            result[key_text] = value_text
            if len(result) >= 100:
                break
        return result

    def fact_text(self) -> str:
        """Факты для проверок: производитель, описание и характеристики.

        Название товара сюда не входит: цифры из артикула (МП3-У → «3»)
        не должны подтверждать произвольные утверждения вроде «класс точности 3».
        """
        parts = [self.manufacturer, self.description]
        parts.extend(self.specs.keys())
        parts.extend(self.specs.values())
        return ". ".join(part for part in parts if part)

    def source_text(self) -> str:
        """Полный вход одной строкой — для отображения и обратной совместимости."""
        parts = [self.name, self.fact_text()]
        return ". ".join(part for part in parts if part)


class SpecItem(BaseModel):
    """Одна характеристика в готовой карточке."""

    name: str
    value: str


class ProductCard(BaseModel):
    """Черновик карточки товара для контент-менеджера."""

    seo_title: str
    seo_description: str
    characteristics: list[SpecItem] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    commercial_block: str = ""
    #: Поля, которых не хватило для полноценной карточки.
    missing_data: list[str] = Field(default_factory=list)
    #: Технические замечания проверок (что пришлось исправить или удалить).
    warnings: list[str] = Field(default_factory=list)


class CompletenessReport(BaseModel):
    """Результат проверки входных данных до обращения к модели."""

    family: str
    family_title: str
    present: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    can_generate: bool = True
