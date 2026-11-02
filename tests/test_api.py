"""Тесты HTTP-слоя. Сеть не используется: провайдер mock."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PRODUCT = {
    "name": "Манометр показывающий МП3-У",
    "manufacturer": "Манотомь",
    "specs": {"Диаметр корпуса": "100 мм", "Класс точности": "1,5"},
    "description": "Прибор для измерения избыточного давления.",
}


def test_providers_endpoint_lists_supported_providers() -> None:
    response = client.get("/api/providers")

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["providers"]]
    assert "mock" in ids
    assert "openai_compatible" in ids


def test_examples_endpoint_returns_bundled_products() -> None:
    response = client.get("/api/examples")

    assert response.status_code == 200
    examples = response.json()["examples"]
    assert len(examples) >= 2
    assert all(example["payload"].get("name") for example in examples)


def test_analyze_reports_missing_fields() -> None:
    response = client.post("/api/analyze", json={"product": PRODUCT})

    assert response.status_code == 200
    report = response.json()
    assert report["family"] == "manometer"
    assert "Диапазон измерений" in report["missing"]


def test_generate_with_mock_provider_returns_card() -> None:
    response = client.post(
        "/api/generate",
        json={"product": PRODUCT, "llm": {"provider": "mock"}},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["card"]["seo_title"]) <= 70
    assert 800 <= len(data["card"]["seo_description"]) <= 1200
    assert data["markdown"].startswith("# ")
    assert json.loads(data["json"])["seo_title"] == data["card"]["seo_title"]


def test_empty_product_name_returns_422_with_field() -> None:
    response = client.post(
        "/api/generate",
        json={"product": {"name": "   ", "specs": {}}, "llm": {"provider": "mock"}},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["field"] == "name"


def test_broken_specs_type_returns_422() -> None:
    response = client.post("/api/analyze", json={"product": {"name": "Прибор", "specs": 42}})

    assert response.status_code == 422
    assert "specs" in response.json()["detail"][0]["field"]


def test_unknown_provider_returns_400() -> None:
    response = client.post(
        "/api/generate",
        json={"product": PRODUCT, "llm": {"provider": "no-such-provider"}},
    )

    assert response.status_code == 400


def test_custom_base_url_does_not_receive_server_key(monkeypatch) -> None:
    """Ключ из .env нельзя увести на чужой endpoint, подставив base_url в запросе."""
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-env-secret-value")

    response = client.post(
        "/api/generate",
        json={
            "product": PRODUCT,
            "llm": {"base_url": "https://example.com/v1"},
        },
    )

    assert response.status_code == 400
    assert "не задан ключ" in response.text.lower()
    assert "sk-env-secret-value" not in response.text


def test_private_base_url_is_rejected() -> None:
    response = client.post(
        "/api/generate",
        json={
            "product": PRODUCT,
            "llm": {"provider": "openai_compatible", "base_url": "http://169.254.169.254/latest"},
        },
    )

    assert response.status_code == 400
    assert "base_url" in response.text


def test_plain_http_for_external_host_is_rejected() -> None:
    response = client.post(
        "/api/generate",
        json={
            "product": PRODUCT,
            "llm": {"provider": "openai_compatible", "base_url": "http://example.com/v1"},
        },
    )

    assert response.status_code == 400


def test_validation_error_does_not_echo_api_key() -> None:
    response = client.post(
        "/api/generate",
        json={"product": PRODUCT, "llm": {"api_key": {"secret": "sk-should-not-be-echoed"}}},
    )

    assert response.status_code == 422
    assert "sk-should-not-be-echoed" not in response.text


def test_oversized_description_is_rejected() -> None:
    response = client.post(
        "/api/analyze",
        json={"product": {"name": "Прибор", "description": "а" * 30000}},
    )

    assert response.status_code == 422


def test_api_key_is_never_echoed_back() -> None:
    response = client.post(
        "/api/generate",
        json={
            "product": PRODUCT,
            "llm": {"provider": "mock", "api_key": "sk-super-secret-value"},
        },
    )

    assert response.status_code == 200
    assert "sk-super-secret-value" not in response.text
