"""Contract tests for model API endpoints and opt-in chaos controls."""

from __future__ import annotations

import model_api.main as main_module
from fastapi.testclient import TestClient
from model_api.deps import ModelApiSettings
from model_api.main import create_app

from tests.fakes import FakeFeatureSource, FakeModelSource


def test_happy_path_preserves_request_id(api_client: TestClient) -> None:
    """A valid batch returns bounded probabilities and correlated metadata."""
    response = api_client.post(
        "/predict",
        headers={"x-request-id": "request-123"},
        json={"customer_ids": [1, 2, 3]},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "request-123"
    body = response.json()
    assert body["request_id"] == "request-123"
    assert body["model_version"] == "test-v7"
    assert [item["customer_id"] for item in body["predictions"]] == [1, 2, 3]
    assert all(0 <= item["default_probability"] <= 1 for item in body["predictions"])


def test_unknown_customer_returns_exact_404_contract(api_client: TestClient) -> None:
    """Any missing feature ID rejects the batch with its unknown IDs."""
    response = api_client.post("/predict", json={"customer_ids": [1, 999]})
    assert response.status_code == 404
    assert response.json() == {"error": "unknown_customers", "ids": [999]}


def test_invalid_batches_return_422(api_client: TestClient) -> None:
    """Empty, oversized, and malformed requests are rejected by Pydantic."""
    assert api_client.post("/predict", json={"customer_ids": []}).status_code == 422
    assert (
        api_client.post("/predict", json={"customer_ids": list(range(101))}).status_code
        == 422
    )
    assert api_client.post("/predict", json={"ids": [1]}).status_code == 422


def test_health_readiness_metrics_and_model_info(api_client: TestClient) -> None:
    """Operational endpoints expose live process, provider, metric, and model state."""
    assert api_client.get("/healthz").json() == {"status": "ok"}
    assert api_client.get("/readyz").json() == {"status": "ok"}
    info = api_client.get("/model-info")
    assert info.status_code == 200
    assert info.json()["version"] == "test-v7"
    metrics = api_client.get("/metrics").text
    assert "http_requests_total" in metrics
    assert 'model_version="test-v7"' in metrics
    assert "feature_fetch_duration_seconds" in metrics


def test_readiness_is_false_before_lifespan_loads_providers() -> None:
    """Readiness fails closed before startup has loaded both providers."""
    application = create_app(
        ModelApiSettings(),
        feature_source=FakeFeatureSource(),
        model_source=FakeModelSource(),
    )
    client = TestClient(application)
    try:
        response = client.get("/readyz")
    finally:
        client.close()
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_unreachable_feature_source_fails_readiness() -> None:
    """A loaded model is not ready when its feature source cannot answer."""
    with TestClient(
        create_app(
            ModelApiSettings(),
            feature_source=FakeFeatureSource(reachable=False),
            model_source=FakeModelSource(),
        )
    ) as client:
        assert client.get("/readyz").status_code == 503


def test_chaos_routes_are_absent_by_default(api_client: TestClient) -> None:
    """Production-safe defaults do not register either chaos endpoint."""
    assert api_client.post("/debug/crash").status_code == 404
    assert api_client.post("/debug/latency?ms=10").status_code == 404


def test_chaos_routes_are_enabled_explicitly(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The local flag registers latency and crash controls."""
    crashes: list[bool] = []
    monkeypatch.setattr(main_module, "crash_process", lambda: crashes.append(True))
    settings = ModelApiSettings(enable_chaos=True)
    with TestClient(
        create_app(
            settings,
            feature_source=FakeFeatureSource(),
            model_source=FakeModelSource(),
        )
    ) as client:
        assert client.post("/debug/latency?ms=25").json() == {"latency_ms": 25}
        assert client.post("/debug/latency?ms=0").json() == {"latency_ms": 0}
        assert client.post("/debug/crash").status_code == 202
    assert crashes == [True]
