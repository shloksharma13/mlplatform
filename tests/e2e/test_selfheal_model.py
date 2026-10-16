"""End-to-end assertion for the drift-triggered model self-healing loop."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.request import urlopen

BASE_URL = os.environ.get("MLP_E2E_URL", "http://127.0.0.1:8080")
MONITOR_URL = "http://localhost:18089"


def _get_json(url: str) -> dict[str, object]:
    """Fetch and decode one JSON endpoint."""
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@contextmanager
def _monitor_port_forward() -> Iterator[None]:
    """Expose the in-cluster monitor endpoint for this test only."""
    process = subprocess.Popen(
        [
            "kubectl",
            "-n",
            "observability",
            "port-forward",
            "svc/drift-monitor",
            "18089:8080",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                _get_json(f"{MONITOR_URL}/state")
                break
            except Exception:
                time.sleep(1)
        else:
            raise AssertionError("drift monitor port-forward did not become ready")
        yield
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_model_self_healing_loop() -> None:
    """Run the headline demo, then verify production and drift state agree."""
    if os.environ.get("MLP_E2E_RUN_DEMO") == "1":
        subprocess.run(["make", "demo-model"], check=True, timeout=2_700)

    model_info = _get_json(f"{BASE_URL}/model-info")
    with _monitor_port_forward():
        state = _get_json(f"{MONITOR_URL}/state")

    assert str(model_info["version"]) == str(state["champion_version"])
    assert state["drifted"] is False
    assert state["insufficient_data"] is False
    assert max(feature["psi"] for feature in state["features"]) < 0.1
