"""Restore the repeatable demo baseline (P8.7, SPEC 6.4).

Regenerates the deterministic baseline data, republishes it, fully
rematerializes the online store (timestamps move backwards, so incremental
materialization would write nothing), repoints the champion alias at the
baseline version, redeploys it, truncates prediction_log, and clears drift
state. SPEC names v1 as the baseline version; v2 superseded it with
bit-identical weights (P7.4 drill a), so the baseline version defaults to 2.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

BASELINE_VERSION = "2"


def _version_exists(version: str) -> bool:
    """Return whether the registry holds this model version."""
    try:
        _mlflow(
            f"/api/2.0/mlflow/model-versions/get?name=loan-default&version={version}"
        )
    except Exception as exc:
        if getattr(exc, "code", None) == 404:
            return False
        raise
    return True


def _champion_version() -> str:
    """Return the version currently holding the champion alias."""
    payload = _mlflow("/api/2.0/mlflow/registered-models/get?name=loan-default")
    for item in json.loads(payload)["registered_model"].get("aliases", []):
        if item["alias"] == "champion":
            return str(item["version"])
    raise ValueError("registry has no champion alias to fall back to")


def _resolve_version(requested: str) -> str:
    """Use the requested baseline unless the registry lacks it (fresh rebuild).

    A clean-room `deploy-all` only creates v1+v2, so a hardcoded default from
    an iterated registry 404s. Fall back to the live champion, which on a
    fresh build is the bit-identical seed-42 baseline.
    """
    if _version_exists(requested):
        return requested
    fallback = _champion_version()
    print(f"baseline v{requested} not found; falling back to champion v{fallback}")
    return fallback


def _mlflow(path: str, method: str = "GET", payload: bytes | None = None) -> bytes:
    """Call the MLflow tracking server from the host (registry APIs only)."""
    request = urllib.request.Request(
        f"{os.environ.get('MLFLOW_TRACKING_URI', 'http://localhost:5000')}{path}",
        data=payload,
        headers={"content-type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _has_alias(alias: str) -> bool:
    """Return whether the registry currently binds one alias."""
    payload = _mlflow("/api/2.0/mlflow/registered-models/get?name=loan-default")
    aliases = json.loads(payload)["registered_model"].get("aliases", [])
    return any(item["alias"] == alias for item in aliases)


def _model_data_end(version: str) -> dt.date:
    """Read the baseline run's data end so reset data matches its reference."""
    model = json.loads(
        _mlflow(
            f"/api/2.0/mlflow/model-versions/get?name=loan-default&version={version}"
        )
    )["model_version"]
    run = json.loads(_mlflow(f"/api/2.0/mlflow/runs/get?run_id={model['run_id']}"))[
        "run"
    ]
    params = {item["key"]: item["value"] for item in run["data"]["params"]}
    data_end = params.get("data_end")
    if not data_end:
        raise ValueError(f"baseline version {version} has no data_end parameter")
    return dt.date.fromisoformat(data_end[:10])


def parse_args() -> argparse.Namespace:
    """Parse reset CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=BASELINE_VERSION)
    return parser.parse_args()


def _recover_pending_staging_release() -> None:
    """Roll staging back to its last deployed revision after an interruption."""
    status = subprocess.run(
        ["helm", "status", "model-api-staging", "-n", "staging"],
        check=False,
        capture_output=True,
        text=True,
    )
    if "pending-" not in status.stdout:
        return
    history = subprocess.run(
        ["helm", "history", "model-api-staging", "-n", "staging", "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    revisions = [
        item["revision"]
        for item in json.loads(history.stdout)
        if item["status"] == "deployed"
    ]
    if not revisions:
        raise RuntimeError("staging release is pending and has no deployed revision")
    revision = max(revisions)
    subprocess.run(
        [
            "helm",
            "rollback",
            "model-api-staging",
            str(revision),
            "-n",
            "staging",
            "--wait",
            "--timeout",
            "5m",
        ],
        check=True,
    )


def main() -> int:
    """Rebuild the baseline and redeploy the baseline champion."""
    args = parse_args()
    version = _resolve_version(args.version)
    end_date = _model_data_end(version)
    _recover_pending_staging_release()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "ml.data.generate",
            "--out",
            "data",
            "--seed",
            "42",
            "--end-date",
            end_date.isoformat(),
        ],
        check=True,
    )
    repo_root = Path(__file__).resolve().parents[1]
    publish_sh = repo_root / "scripts" / "publish_feast_data.sh"
    run_job_sh = repo_root / "scripts" / "run_job.sh"
    remat_yaml = repo_root / "infra" / "jobs" / "feast-rematerialize-job.yaml"
    deploy_sh = repo_root / "scripts" / "deploy.sh"

    subprocess.run(["bash", str(publish_sh)], check=True)
    # Feast's full rematerialization does not overwrite Redis keys whose
    # timestamps are newer than the reset snapshot.  Flush the demo-only
    # ephemeral online store so no drifted values survive the reset.
    subprocess.run(
        [
            "kubectl",
            "-n",
            "data",
            "exec",
            "deploy/redis",
            "--",
            "redis-cli",
            "FLUSHALL",
        ],
        check=True,
    )
    subprocess.run(
        ["bash", str(run_job_sh), str(remat_yaml)],
        check=True,
    )

    _mlflow(
        "/api/2.0/mlflow/registered-models/alias",
        method="POST",
        payload=(
            f'{{"name":"loan-default","alias":"champion","version":"{version}"}}'
        ).encode(),
    )
    _mlflow(
        "/api/2.0/mlflow/registered-models/alias",
        method="DELETE",
        payload=b'{"name":"loan-default","alias":"candidate"}',
    ) if _has_alias("candidate") else None
    subprocess.run(
        [
            "bash",
            str(deploy_sh),
            "model-api",
            "serving",
            os.environ.get("IMAGE_TAG", "dev"),
            "--set",
            f"model.version={version}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "kubectl",
            "-n",
            "data",
            "delete",
            "job",
            "-l",
            "app=retrain",
            "--ignore-not-found",
            "--wait=true",
        ],
        check=True,
    )
    subprocess.run(
        [
            "kubectl",
            "-n",
            "data",
            "exec",
            "postgres-0",
            "--",
            "psql",
            "-U",
            "mlp",
            "-d",
            "mlp",
            "-c",
            "TRUNCATE prediction_log; DELETE FROM drift_state; DELETE FROM drift_meta;",
        ],
        check=True,
    )
    subprocess.run(
        [
            "kubectl",
            "-n",
            "observability",
            "rollout",
            "restart",
            "deployment/drift-monitor",
        ],
        check=True,
    )
    subprocess.run(
        [
            "kubectl",
            "-n",
            "observability",
            "rollout",
            "status",
            "deployment/drift-monitor",
            "--timeout=120s",
        ],
        check=True,
    )
    print(f"reset_demo complete: baseline data through {end_date}, champion v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
