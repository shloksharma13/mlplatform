"""End-to-end pod-recovery test for the serving deployment."""

from __future__ import annotations

import json
import subprocess
import time
from urllib.request import Request, urlopen

NAMESPACE = "serving"
SELECTOR = "app.kubernetes.io/instance=model-api"
BASE_URL = "http://127.0.0.1:8080"


def _kubectl(*args: str) -> str:
    """Run kubectl and return stdout, failing on an invalid cluster operation."""
    result = subprocess.run(
        ["kubectl", *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _predict_status() -> int:
    """Return the HTTP status for one serving request."""
    request = Request(
        f"{BASE_URL}/predict",
        data=json.dumps({"customer_ids": [1]}).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status
    except Exception as exc:
        return int(getattr(exc, "code", 0) or 0)


def test_serving_survives_pod_deletion() -> None:
    """Delete one replica, exercise traffic, and enforce a one-percent budget."""
    victim = _kubectl(
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        SELECTOR,
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    replicas = int(
        _kubectl(
            "get",
            "deployment",
            "-n",
            NAMESPACE,
            "-l",
            SELECTOR,
            "-o",
            "jsonpath={.items[0].spec.replicas}",
        )
    )
    assert replicas >= 2

    _kubectl("delete", "pod", victim, "-n", NAMESPACE)
    statuses = [_predict_status() for _ in range(30)]
    errors = sum(status != 200 for status in statuses)
    assert errors / len(statuses) <= 0.01, {
        "victim": victim,
        "errors": errors,
        "statuses": statuses,
    }

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        ready = _kubectl(
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-l",
            SELECTOR,
            "-o",
            "jsonpath={range .items[*]}{.status.containerStatuses[0].ready}"
            "{'\\n'}{end}",
        )
        if sum(value == "true" for value in ready.splitlines()) >= 2:
            break
        time.sleep(2)
    else:
        raise AssertionError("serving deployment did not recover all replicas")
