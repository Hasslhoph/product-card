"""Схемы данных: входной товар, карточка товара, отчёт о полноте."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Значение-заглушка для отсутствующих данных. Требование задания:
#: если характеристики нет во входных данных, модель не должна её придумывать.
MISSING_VALUE = "данных нет"


class ProductInput(BaseModel):
    """Товар на входе. Ровно та структура, что описана в задании.

    Дополнительно принимает ``specs`` списком пар — так характеристики
    удобнее присылать из таблицы или парсера.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, description="Название товара")
    manufacturer: str = Field(default="", description="Производитель")
    specs: dict[str, str] = Field(
        default_factory=dict, description="Краткие характеристики: название → значение"
    )
    description: str = Field(default="", description="Описание или фрагмент техпаспорта")

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
            raise ValueError("specs должен быть объектом или списком пар")

        result: dict[str, str] = {}
        for key, value in raw:
            if key is None or value is None:
                continue
            key_text = str(key).strip()
            value_text = str(value).strip()
            if key_text and value_text:
                result[key_text] = value_text
        return result

    def source_text(self) -> str:
        """Всё, что было на входе, одной строкой — источник правды для проверок."""
        parts = [self.name, self.manufacturer, self.description]
        parts.extend(self.specs.keys())
        parts.extend(self.specs.values())
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
