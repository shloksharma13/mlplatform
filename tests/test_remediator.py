"""Unit tests for the remediator webhook with mocked Kubernetes clients."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import remediator.main as remediator_main
from fastapi.testclient import TestClient
from remediator.main import RemediatorSettings, create_app


def _hpa(min_replicas: int, max_replicas: int) -> SimpleNamespace:
    return SimpleNamespace(
        spec=SimpleNamespace(min_replicas=min_replicas, max_replicas=max_replicas)
    )


@pytest.fixture()
def mocked_app(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build the app with mocked K8s APIs and Grafana annotations."""
    apps_api = MagicMock()
    # A fresh runner/node may have monotonic uptime below the debounce window.
    monkeypatch.setattr(remediator_main, "time", SimpleNamespace(monotonic=lambda: 0.0))
    autoscaling_api = MagicMock()
    autoscaling_api.read_namespaced_horizontal_pod_autoscaler.return_value = _hpa(2, 6)
    monkeypatch.setattr(
        remediator_main.remediation,
        "create_grafana_annotation",
        lambda *args, **kwargs: 42,
    )
    settings = RemediatorSettings(grafana_password="test")
    application = create_app(
        settings, apps_api=apps_api, autoscaling_api=autoscaling_api
    )
    client = TestClient(application)
    client.apps_api = apps_api  # type: ignore[attr-defined]
    client.autoscaling_api = autoscaling_api  # type: ignore[attr-defined]
    return client


def _payload(name: str, status: str = "firing", fingerprint: str = "") -> dict:
    return {
        "alerts": [
            {
                "status": status,
                "fingerprint": fingerprint or f"fp-{name}-{status}",
                "labels": {"alertname": name, "severity": "warning"},
                "annotations": {"summary": f"{name} test"},
            }
        ]
    }


def test_probes_and_metrics(mocked_app: TestClient) -> None:
    """Liveness, readiness, and Prometheus metrics are exposed."""
    assert mocked_app.get("/healthz").json() == {"status": "ok"}
    assert mocked_app.get("/readyz").json() == {"status": "ready"}
    body = mocked_app.get("/metrics").text
    assert "remediator_actions_total" in body


def test_high_latency_raises_hpa_floor(mocked_app: TestClient) -> None:
    """HighP95Latency patches minReplicas +2 (capped at max) and annotates."""
    response = mocked_app.post("/alert", json=_payload("HighP95Latency"))
    assert response.status_code == 200
    outcome = response.json()["outcomes"][0]
    assert outcome["action"] == "scale_hpa_floor"
    assert "min=4" in outcome["result"]
    mocked_app.autoscaling_api.patch_namespaced_horizontal_pod_autoscaler.assert_called_once()
    _, _, body = (
        mocked_app.autoscaling_api.patch_namespaced_horizontal_pod_autoscaler.call_args[
            0
        ]
    )
    assert body == {"spec": {"minReplicas": 4}}


def test_hpa_floor_caps_at_max(mocked_app: TestClient) -> None:
    """The HPA floor never exceeds maxReplicas."""
    reader = mocked_app.autoscaling_api.read_namespaced_horizontal_pod_autoscaler
    reader.return_value = _hpa(5, 6)
    response = mocked_app.post(
        "/alert", json=_payload("HighP95Latency", fingerprint="fp-cap")
    )
    assert "min=6" in response.json()["outcomes"][0]["result"]


def test_debounce_ignores_repeat(mocked_app: TestClient) -> None:
    """The same fingerprint within 2 minutes is skipped."""
    first = mocked_app.post(
        "/alert", json=_payload("HighP95Latency", fingerprint="fp-dup")
    )
    second = mocked_app.post(
        "/alert", json=_payload("HighP95Latency", fingerprint="fp-dup")
    )
    assert first.json()["outcomes"][0]["action"] == "scale_hpa_floor"
    assert second.json()["outcomes"][0]["action"] == "debounced"
    assert (
        mocked_app.autoscaling_api.patch_namespaced_horizontal_pod_autoscaler.call_count
        == 1
    )


def test_debounce_expires_from_zero_uptime(
    mocked_app: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first alert at uptime zero runs, repeats wait, and the window expires."""
    clock = [0.0]
    monkeypatch.setattr(
        remediator_main, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    payload = _payload("HighP95Latency", fingerprint="fp-clock")
    first = mocked_app.post("/alert", json=payload)
    clock[0] = 119.9
    repeated = mocked_app.post("/alert", json=payload)
    clock[0] = 120.0
    expired = mocked_app.post("/alert", json=payload)
    assert first.json()["outcomes"][0]["action"] == "scale_hpa_floor"
    assert repeated.json()["outcomes"][0]["action"] == "debounced"
    assert expired.json()["outcomes"][0]["action"] == "scale_hpa_floor"
    assert (
        mocked_app.autoscaling_api.patch_namespaced_horizontal_pod_autoscaler.call_count
        == 2
    )


def test_model_api_down_restarts_and_annotates(mocked_app: TestClient) -> None:
    """ModelApiDown triggers a rollout restart plus a Grafana annotation."""
    response = mocked_app.post("/alert", json=_payload("ModelApiDown"))
    outcome = response.json()["outcomes"][0]
    assert outcome["action"] == "restart_and_annotate"
    mocked_app.apps_api.patch_namespaced_deployment.assert_called_once()


def test_drift_only_annotates(mocked_app: TestClient) -> None:
    """ModelDriftDetected never touches workloads (retrain is automatic)."""
    response = mocked_app.post("/alert", json=_payload("ModelDriftDetected"))
    assert response.json()["outcomes"][0]["action"] == "annotate"
    mocked_app.apps_api.patch_namespaced_deployment.assert_not_called()
    mocked_app.autoscaling_api.patch_namespaced_horizontal_pod_autoscaler.assert_not_called()


def test_unknown_alert_ignored(mocked_app: TestClient) -> None:
    """Unmapped alerts are logged and ignored."""
    response = mocked_app.post("/alert", json=_payload("SomethingElse"))
    assert response.json()["outcomes"][0]["action"] == "none"
