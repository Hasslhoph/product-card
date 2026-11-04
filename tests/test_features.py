"""Тесты новых возможностей: сессии с ключом, список моделей, текстовый формат."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMRequest
from app.main import LLMOptions, _resolve_options, app
from app.render import to_plain_text
from app.sessions import ClientSession, SessionStore, mask_key, session_view

PRODUCT = {
    "name": "Манометр показывающий МП3-У",
    "manufacturer": "Манотомь",
    "specs": {"Диаметр корпуса": "100 мм"},
    "description": "Прибор для измерения избыточного давления.",
}


# ---------------------------------------------------------------- сессии


def test_mask_key_shows_only_tail() -> None:
    assert mask_key("sk-1234567890abcd") == "••••abcd"
    assert mask_key("") == ""
    assert mask_key("short") == "••••"


def test_session_view_never_returns_raw_key() -> None:
    view = session_view(ClientSession(api_key="sk-super-secret-value"))

    assert view["has_key"] is True
    assert "sk-super-secret-value" not in str(view)


def test_session_store_expires_by_ttl() -> None:
    store = SessionStore(ttl=10)
    store.update("id-1", api_key="sk-1")

    store._items["id-1"].last_used = time.time() - 11

    assert store.get("id-1") is None


def test_session_store_clears_key() -> None:
    store = SessionStore()
    store.update("id-2", api_key="sk-2")

    store.clear("id-2")

    assert store.get("id-2") is None


def test_session_endpoint_saves_key_and_hides_it() -> None:
    with TestClient(app) as client:
        saved = client.post(
            "/api/session",
            json={
                "provider": "openai_compatible",
                "api_key": "sk-session-secret-1234",
                "model": "deepseek-v4.1-flash",
                "base_url": "https://example.com/v1",
            },
        )

        assert saved.status_code == 200
        assert "sk-session-secret-1234" not in saved.text
        assert saved.json()["has_key"] is True
        assert saved.json()["masked"].endswith("1234")

        state = client.get("/api/session").json()
        assert state["has_key"] is True
        assert state["model"] == "deepseek-v4.1-flash"

        cleared = client.delete("/api/session").json()
        assert cleared["has_key"] is False


# ---------------------------------------------------- приоритет источников


def test_session_key_is_used_for_its_own_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-from-env")
    session = ClientSession(
        provider="openai_compatible",
        api_key="sk-from-session",
        base_url="https://api.example.com/v1",
    )

    options = _resolve_options(LLMOptions(provider="openai_compatible"), session)

    assert options["api_key"] == "sk-from-session"
    assert options["base_url"] == "https://api.example.com/v1"


def test_session_key_is_not_sent_to_another_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-from-env")
    session = ClientSession(
        provider="openai_compatible",
        api_key="sk-from-session",
        base_url="https://api.example.com/v1",
    )

    options = _resolve_options(
        LLMOptions(provider="openai_compatible", base_url="https://example.com/v1"), session
    )

    assert options["api_key"] == ""


def test_request_key_wins_over_session(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    session = ClientSession(
        provider="openai_compatible",
        api_key="sk-from-session",
        base_url="https://api.example.com/v1",
    )

    options = _resolve_options(
        LLMOptions(provider="openai_compatible", api_key="sk-from-request"), session
    )

    assert options["api_key"] == "sk-from-request"


# ------------------------------------------------------------- список моделей


def test_models_endpoint_returns_catalog_without_key() -> None:
    with TestClient(app) as client:
        response = client.post("/api/models", json={"provider": "openai"})

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "catalog"
    assert "gpt-4o-mini" in data["models"]
    assert data["note"]


def test_models_endpoint_returns_mock_model() -> None:
    with TestClient(app) as client:
        response = client.post("/api/models", json={"provider": "mock"})

    assert response.json()["models"] == ["mock"]


def test_models_endpoint_falls_back_to_catalog_on_error(monkeypatch) -> None:
    async def broken(*args, **kwargs):
        from app.model_catalog import ModelListError

        raise ModelListError("провайдер недоступен")

    monkeypatch.setattr("app.main.fetch_models", broken)

    with TestClient(app) as client:
        client.post("/api/session", json={"provider": "openai", "api_key": "sk-test-123456"})
        response = client.post("/api/models", json={"provider": "openai"})

    data = response.json()
    assert data["source"] == "catalog"
    assert "недоступен" in data["note"]


def test_models_endpoint_uses_provider_list(monkeypatch) -> None:
    async def fake(*args, **kwargs):
        return ["gpt-4.1", "gpt-4o-mini"]

    monkeypatch.setattr("app.main.fetch_models", fake)

    with TestClient(app) as client:
        client.post("/api/session", json={"provider": "openai", "api_key": "sk-test-123456"})
        response = client.post("/api/models", json={"provider": "openai"})

    data = response.json()
    assert data["source"] == "provider"
    assert data["models"][:2] == ["gpt-4.1", "gpt-4o-mini"]
    # Справочник дополняет список провайдера, а не заменяет его.
    assert len(data["models"]) >= 2


# --------------------------------------------------------------- форматы


def test_generate_returns_three_formats() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/generate", json={"product": PRODUCT, "llm": {"provider": "mock"}}
        )

    data = response.json()
    assert data["markdown"].startswith("# ")
    assert data["text"].startswith("Карточка товара:")
    assert "#" not in data["text"]
    assert "|" not in data["text"]
    assert '"seo_title"' in data["json"]


def test_plain_text_contains_required_sections() -> None:
    from app.models import ProductCard, ProductInput, SpecItem

    card = ProductCard(
        seo_title="Манометр МП3-У",
        seo_description="Описание прибора.",
        characteristics=[SpecItem(name="Диаметр корпуса", value="100 мм")],
        search_queries=["манометр купить"],
        commercial_block="Подходит для инженерных систем.",
    )
    product = ProductInput.model_validate(PRODUCT)

    text = to_plain_text(card, product)
    sections = text.split("\n\n")
    headings = {line for line in text.splitlines() if line.isupper() or "(" in line}

    assert "Карточка товара:" in text
    assert "Диаметр корпуса: 100 мм" in text
    assert any("SEO" in line for line in headings)
    assert len(sections) > 3
