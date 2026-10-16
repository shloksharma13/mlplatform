"""End-to-end checks for the public model-serving contract."""

from __future__ import annotations

import json
import os
import time
from time import monotonic
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("MLP_E2E_URL", "http://127.0.0.1:8080")


def _request(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, object] | None, str]:
    """Issue one JSON request and return status, decoded body, and raw text."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"content-type": "application/json"},
        method=method,
    )
    last_error = "request failed"
    for attempt in range(3):
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
                try:
                    decoded = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    decoded = None
                return response.status, decoded, raw
        except Exception as exc:
            response = getattr(exc, "code", None)
            last_error = (
                exc.read().decode("utf-8") if hasattr(exc, "read") else str(exc)
            )
            if response is not None:
                return int(response), None, last_error
            if attempt < 2:
                time.sleep(1)
    return 0, None, last_error


def test_serving_contract() -> None:
    """Verify health, readiness, metrics, model metadata, and prediction."""
    for path in ("/healthz", "/readyz"):
        status, body, _ = _request(path)
        assert status == 200, (path, body)
        assert body == {"status": "ok"}

    status, model_info, _ = _request("/model-info")
    assert status == 200
    assert model_info is not None
    assert model_info["name"] == "loan-default"
    assert str(model_info["version"])
    assert model_info["features"]

    status, _, metrics = _request("/metrics")
    assert status == 200
    assert "http_requests_total" in metrics

    deadline = monotonic() + 180
    while monotonic() < deadline:
        info_status, current_info, _ = _request("/model-info")
        prediction_status, prediction, _ = _request(
            "/predict", method="POST", body={"customer_ids": [1, 2, 3]}
        )
        if (
            info_status == 200
            and current_info is not None
            and prediction_status == 200
            and prediction is not None
            and str(prediction["model_version"]) == str(current_info["version"])
        ):
            model_info = current_info
            break
        time.sleep(2)
    else:
        raise AssertionError("model-info and predict did not converge on one version")

    assert len(prediction["predictions"]) == 3
    assert all("default_probability" in item for item in prediction["predictions"])
