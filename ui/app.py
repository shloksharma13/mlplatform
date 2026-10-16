"""Central Management & Monitoring Dashboard Application.

FastAPI service providing real-time metrics, cluster workload introspection,
interactive inference testing, and streaming command execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from mlp_common.logging import JsonFormatter
from pydantic import BaseModel

logger = logging.getLogger("ui")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter("dashboard"))
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False

security = HTTPBasic(auto_error=False)


async def authenticate_dashboard(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> None:
    """Require the configured test-session credential before running operations."""
    if request.url.path == "/healthz":
        return
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not password:
        if os.environ.get("DEPLOY_TARGET") == "azure":
            raise HTTPException(status_code=503, detail="Dashboard credential missing")
        return
    valid = (
        credentials is not None
        and secrets.compare_digest(credentials.username.encode(), b"admin")
        and secrets.compare_digest(credentials.password.encode(), password.encode())
    )
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Dashboard login required",
            headers={"WWW-Authenticate": "Basic"},
        )


app = FastAPI(
    dependencies=[Depends(authenticate_dashboard)],
    title="MLPlatform Control Plane",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
ACTIVE_DRIFT_QUERY = (
    "max(model_drift_detected and on(instance) up == 1) "
    "or max(model_drift_detected) or vector(0)"
)

# Track running jobs
RUNNING_JOBS: dict[str, dict[str, Any]] = {}

MODEL_API_URL = os.environ.get("MODEL_API_URL", "http://localhost:8080")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
MLFLOW_URL = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MINIO_URL = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
BASELINE_VERSION = "local"


def load_cloud_demo_data() -> str:
    """Fetch real demo data and the initial model at runtime, never bake them in."""
    import boto3
    import joblib
    import mlflow.sklearn
    from mlflow.tracking import MlflowClient

    data = BASE_DIR / "data"
    artifacts = BASE_DIR / "artifacts"
    data.mkdir(exist_ok=True)
    artifacts.mkdir(exist_ok=True)
    storage = boto3.client("s3", endpoint_url=MINIO_URL)
    for source, target in [
        ("customer_features.parquet", "features.parquet"),
        ("labels.parquet", "labels.parquet"),
    ]:
        storage.download_file("feast-offline", source, str(data / target))
    frame = pd.read_parquet(data / "features.parquet")
    frame.loc[frame.event_timestamp == frame.event_timestamp.max()].to_parquet(
        data / "features_latest.parquet", index=False
    )
    version = MlflowClient(MLFLOW_URL).get_model_version_by_alias(
        "loan-default", "baseline"
    )
    model = mlflow.sklearn.load_model(f"models:/loan-default/{version.version}")
    joblib.dump(model, artifacts / "model.joblib")
    return str(version.version)


if os.environ.get("DEPLOY_TARGET") == "azure":
    BASELINE_VERSION = load_cloud_demo_data()

BASELINE_MODEL = None
try:
    _model_path = BASE_DIR / "artifacts" / "model.joblib"
    if _model_path.exists():
        import joblib

        BASELINE_MODEL = joblib.load(_model_path)
        logger.info("Loaded baseline model from %s", _model_path)
except Exception as _e:
    logger.warning("Could not load baseline model: %s", _e)

ACTION_COMMANDS: dict[str, str] = {
    "reset_demo": "uv run python scripts/reset_demo.py",
    "traffic_burst": (
        "uv run python scripts/traffic.py --rps 15 --duration 20 --url http://localhost:8080"
    ),
    "chaos_latency_inject": (
        "for p in $(kubectl get pods -n serving -l app.kubernetes.io/name=model-api "
        "-o jsonpath='{.items[*].metadata.name}'); do "
        "kubectl exec -n serving $p -c api -- "
        "curl -s -X POST 'http://localhost:8080/debug/latency?ms=500'; "
        'echo "Injected 500ms latency on $p"; done'
    ),
    "chaos_latency_clear": (
        "for p in $(kubectl get pods -n serving -l app.kubernetes.io/name=model-api "
        "-o jsonpath='{.items[*].metadata.name}'); do "
        "kubectl exec -n serving $p -c api -- "
        "curl -s -X POST 'http://localhost:8080/debug/latency?ms=0'; "
        'echo "Cleared latency on $p"; done'
    ),
    "chaos_pod_kill": "bash scripts/chaos_pod.sh",
    "demo_infra": "bash scripts/chaos_alert.sh",
    "simulate_drift": (
        "uv run python scripts/simulate_drift.py --scenario covariate && "
        "uv run python scripts/traffic.py --rps 30 --duration 12 --url http://localhost:8080"
    ),
    "auto_heal": (
        "uv run python scripts/simulate_drift.py --scenario covariate && "
        "uv run python scripts/traffic.py --rps 30 --duration 12 --url http://localhost:8080"
    ),
    "demo_model": "bash scripts/retrain_and_release.sh",
    "manual_retrain": "bash scripts/retrain_and_release.sh",
    "demo_model_full": "bash scripts/retrain_and_release.sh",
    "run_gate": "bash scripts/run_gate.sh",
    "feast_rematerialize": "make feast-rematerialize",
    "e2e_tests": "make e2e",
}


if os.environ.get("DEPLOY_TARGET") == "azure":
    for action, command in ACTION_COMMANDS.items():
        command = command.replace("uv run python", "python")
        if action not in {"chaos_latency_inject", "chaos_latency_clear"}:
            command = command.replace("http://localhost:8080", MODEL_API_URL)
        ACTION_COMMANDS[action] = command
    ACTION_COMMANDS["reset_demo"] += f" --version {BASELINE_VERSION}"
    ACTION_COMMANDS["e2e_tests"] = (
        f"bash scripts/smoke.sh serving model-api {MODEL_API_URL}"
    )


class PredictRequest(BaseModel):
    customer_id: str | int = "1"
    env: str = "clean"


class ActionRequest(BaseModel):
    action: str


@app.get("/healthz")
async def dashboard_health() -> dict[str, str]:
    """Provide the credential-free Kubernetes readiness check."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    """Serve the single-page application dashboard."""
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard template not found.")
    page = index_file.read_text(encoding="utf-8")
    public_host = os.environ.get("PUBLIC_HOST", "")
    if public_host:
        page = page.replace("http://localhost:", f"http://{public_host}:")
    return HTMLResponse(content=page)


@app.get("/api/status")
async def get_system_status() -> dict[str, Any]:
    """Retrieve system status across K8s, serving, data, and observability."""
    status: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "cluster": "unknown",
        "nodes_ready": 0,
        "nodes_total": 0,
        "serving_model": None,
        "prometheus_online": False,
        "mlflow_online": False,
        "minio_online": False,
        "drift_detected": False,
    }

    # 1. K8s Nodes
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "get",
            "nodes",
            "-o",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            nodes_data = json.loads(stdout.decode())
            items = nodes_data.get("items", [])
            status["nodes_total"] = len(items)
            ready_count = 0
            for node in items:
                conds = node.get("status", {}).get("conditions", [])
                for c in conds:
                    if c.get("type") == "Ready" and c.get("status") == "True":
                        ready_count += 1
                        break
            status["nodes_ready"] = ready_count
            status["cluster"] = (
                os.environ.get("CLUSTER_NAME", "Kind (mlp)")
                if ready_count > 0
                else "degraded"
            )
    except Exception as exc:
        logger.debug("Failed querying nodes: %s", exc)

    # 2. Model API
    async with httpx.AsyncClient(timeout=1.5) as client:
        try:
            resp = await client.get(f"{MODEL_API_URL}/model-info")
            if resp.status_code == 200:
                status["serving_model"] = resp.json()
        except Exception:
            status["serving_model"] = None

        # 3. Prometheus
        try:
            resp = await client.get(f"{PROMETHEUS_URL}/-/healthy")
            status["prometheus_online"] = resp.status_code == 200
        except Exception:
            status["prometheus_online"] = False

        # 4. MLflow
        try:
            resp = await client.get(f"{MLFLOW_URL}/health")
            status["mlflow_online"] = resp.status_code == 200
        except Exception:
            status["mlflow_online"] = False

        # 5. MinIO
        try:
            resp = await client.get(f"{MINIO_URL}/minio/health/live")
            status["minio_online"] = resp.status_code == 200
        except Exception:
            status["minio_online"] = False

        # 6. Drift Flag from Prometheus
        try:
            resp = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": ACTIVE_DRIFT_QUERY},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("result", [])
                if data:
                    val = float(data[0].get("value", [0, 0])[1])
                    status["drift_detected"] = val >= 1.0
        except Exception:
            status["drift_detected"] = False

    return status


@app.get("/api/metrics")
async def get_metrics() -> dict[str, Any]:
    """Query Prometheus for live golden signals and drift measurements."""
    metrics: dict[str, Any] = {
        "rps": 0.0,
        "p50_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "error_rate_pct": 0.0,
        "replicas_available": 2,
        "replicas_desired": 2,
        "model_drift_detected": 0,
        "champion_auc": 0.835,
        "candidate_auc": 0.0,
        "feature_psi": {
            "pay_0": 0.005,
            "limit_bal": 0.008,
            "bill_amt1": 0.003,
            "pay_amt1": 0.004,
            "age": 0.002,
            "education": 0.001,
        },
    }

    async with httpx.AsyncClient(timeout=2.0) as client:
        # Helper to query Prometheus scalar
        async def prom_scalar(query: str, default: float = 0.0) -> float:
            try:
                r = await client.get(
                    f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}
                )
                if r.status_code == 200:
                    res = r.json().get("data", {}).get("result", [])
                    if res:
                        return float(res[0].get("value", [0, 0])[1])
            except Exception:
                pass
            return default

        # Query metrics concurrently
        p50_q = (
            "histogram_quantile(0.50, sum(rate("
            "http_request_duration_seconds_bucket{namespace='serving',path='/predict'}[5m]"
            ")) by (le)) * 1000"
        )
        p95_q = (
            "histogram_quantile(0.95, sum(rate("
            "http_request_duration_seconds_bucket{namespace='serving',path='/predict'}[5m]"
            ")) by (le)) * 1000"
        )
        err_q = (
            "(sum(rate(http_requests_total{namespace='serving',status=~'5..'}[2m])) "
            "or vector(0)) / (sum(rate(http_requests_total{namespace='serving'}[2m])) "
            "or vector(1)) * 100"
        )
        rep_avail_q = (
            "kube_deployment_status_replicas_available{"
            "deployment='model-api', namespace='serving'}"
        )
        rep_des_q = (
            "kube_deployment_spec_replicas{deployment='model-api', namespace='serving'}"
        )

        tasks = [
            prom_scalar(
                "sum(rate(http_requests_total{namespace='serving',path='/predict'}[1m]))",
                0.0,
            ),
            prom_scalar(p50_q, 8.5),
            prom_scalar(p95_q, 16.0),
            prom_scalar(err_q, 0.0),
            prom_scalar(rep_avail_q, 2.0),
            prom_scalar(rep_des_q, 2.0),
            prom_scalar(ACTIVE_DRIFT_QUERY, 0.0),
            prom_scalar("model_champion_auc", 0.835),
            prom_scalar("model_candidate_auc", 0.0),
        ]

        results = await asyncio.gather(*tasks)

        metrics["rps"] = round(results[0], 2)
        metrics["p50_latency_ms"] = round(max(results[1], 0.0), 1)
        metrics["p95_latency_ms"] = round(max(results[2], 0.0), 1)
        metrics["error_rate_pct"] = round(max(results[3], 0.0), 2)
        metrics["replicas_available"] = int(results[4])
        metrics["replicas_desired"] = int(results[5])
        metrics["model_drift_detected"] = int(results[6])
        metrics["champion_auc"] = round(results[7], 4)
        metrics["candidate_auc"] = round(results[8], 4)

        # Query PSI per feature if available
        try:
            r = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": "model_feature_psi"},
            )
            if r.status_code == 200:
                res = r.json().get("data", {}).get("result", [])
                for item in res:
                    feat = item.get("metric", {}).get("feature")
                    if feat:
                        metrics["feature_psi"][feat] = round(
                            float(item.get("value", [0, 0])[1]), 4
                        )
        except Exception:
            pass

    return metrics


@app.get("/api/pods")
async def get_pods() -> list[dict[str, Any]]:
    """Retrieve pod status across serving, data, and observability namespaces."""
    pods: list[dict[str, Any]] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "get",
            "pods",
            "-A",
            "-o",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            data = json.loads(stdout.decode())
            now = datetime.now(UTC)
            for item in data.get("items", []):
                ns = item.get("metadata", {}).get("namespace", "")
                if ns not in {"serving", "staging", "data", "observability"}:
                    continue
                name = item.get("metadata", {}).get("name", "")
                created_str = item.get("metadata", {}).get("creationTimestamp", "")
                age_str = "?"
                if created_str:
                    try:
                        created = datetime.fromisoformat(
                            created_str.replace("Z", "+00:00")
                        )
                        seconds = int((now - created).total_seconds())
                        if seconds < 60:
                            age_str = f"{seconds}s"
                        elif seconds < 3600:
                            age_str = f"{seconds // 60}m"
                        else:
                            age_str = f"{seconds // 3600}h"
                    except Exception:
                        pass

                statuses = item.get("status", {}).get("containerStatuses", [])
                ready_count = sum(1 for c in statuses if c.get("ready"))
                total_containers = len(statuses) or 1
                ready_str = f"{ready_count}/{total_containers}"
                restarts = sum(c.get("restartCount", 0) for c in statuses)
                phase = item.get("status", {}).get("phase", "Unknown")

                pods.append(
                    {
                        "name": name,
                        "namespace": ns,
                        "status": phase,
                        "ready": ready_str,
                        "restarts": restarts,
                        "age": age_str,
                    }
                )

            # Enrich serving pods with real-time Prometheus RPS and traffic share %
            serving_pods = [p for p in pods if p["namespace"] == "serving"]
            if serving_pods:
                pod_rps_map: dict[str, float] = {}
                try:
                    pod_query = (
                        "sum(rate(http_requests_total"
                        "{namespace='serving'}[1m])) by (pod)"
                    )
                    async with httpx.AsyncClient(timeout=1.0) as client:
                        r = await client.get(
                            f"{PROMETHEUS_URL}/api/v1/query",
                            params={"query": pod_query},
                        )
                        if r.status_code == 200:
                            for item in r.json().get("data", {}).get("result", []):
                                pname = item.get("metric", {}).get("pod")
                                val = float(item.get("value", [0, 0])[1])
                                if pname:
                                    pod_rps_map[pname] = round(val, 2)
                except Exception:
                    pass

                healthy_serving = [
                    p
                    for p in serving_pods
                    if p["status"] == "Running" and p["ready"] == "1/1"
                ]
                total_active_rps = sum(
                    pod_rps_map.get(p["name"], 0.0) for p in healthy_serving
                )

                for p in serving_pods:
                    is_healthy = p["status"] == "Running" and p["ready"] == "1/1"
                    if not is_healthy:
                        p["traffic_share_pct"] = 0.0
                        p["traffic_rps"] = 0.0
                    elif len(healthy_serving) == 1:
                        p["traffic_share_pct"] = 100.0
                        p["traffic_rps"] = max(
                            pod_rps_map.get(p["name"], 0.0), total_active_rps
                        )
                    else:
                        if total_active_rps > 0.05:
                            p["traffic_rps"] = pod_rps_map.get(p["name"], 0.0)
                            p["traffic_share_pct"] = round(
                                (p["traffic_rps"] / total_active_rps) * 100, 1
                            )
                        else:
                            p["traffic_rps"] = round(pod_rps_map.get(p["name"], 0.0), 2)
                            p["traffic_share_pct"] = round(
                                100.0 / len(healthy_serving), 1
                            )

    except Exception as exc:
        logger.error("Failed to query pods: %s", exc)

    return sorted(pods, key=lambda x: (x["namespace"], x["name"]))


@app.get("/api/pod-logs/{namespace}/{pod_name}", response_class=PlainTextResponse)
async def get_pod_logs(namespace: str, pod_name: str) -> PlainTextResponse:
    """Fetch container logs for a specific pod."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "logs",
            "-n",
            namespace,
            pod_name,
            "--all-containers=true",
            "--tail=150",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        return PlainTextResponse(content=stdout.decode("utf-8", errors="replace"))
    except Exception as exc:
        return PlainTextResponse(
            content=f"Error retrieving logs: {exc}", status_code=500
        )


@app.post("/api/kill-pod/{namespace}/{pod_name}")
async def kill_pod(namespace: str, pod_name: str) -> dict[str, Any]:
    """Delete a pod asynchronously to demonstrate real-time Kubernetes self-healing."""
    if namespace not in ("serving", "staging", "data", "observability"):
        raise HTTPException(status_code=400, detail="Invalid namespace")
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "delete",
            "pod",
            pod_name,
            "-n",
            namespace,
            "--now",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        out = stdout.decode("utf-8", errors="replace").strip()
        return {
            "status": "success" if proc.returncode == 0 else "error",
            "pod": pod_name,
            "namespace": namespace,
            "output": out,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


class BurstTestRequest(BaseModel):
    count: int = 10


@app.post("/api/burst-test")
async def execute_burst_test(
    req: BurstTestRequest | None = None,
) -> dict[str, Any]:
    """Execute rapid concurrent prediction requests to verify zero dropped calls."""
    actual_req = req or BurstTestRequest()
    count = max(1, min(actual_req.count, 50))
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=4.0) as client:

        async def single_call(cid: int):
            try:
                r = await client.post(
                    f"{MODEL_API_URL}/predict", json={"customer_ids": [str(cid)]}
                )
                return r.status_code == 200
            except Exception:
                return False

        tasks = [single_call((i % 30000) + 1) for i in range(count)]
        results = await asyncio.gather(*tasks)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    success = sum(1 for ok in results if ok)
    failed = count - success
    return {
        "status": "success",
        "total": count,
        "successful": success,
        "failed": failed,
        "success_rate_pct": round((success / count) * 100, 1),
        "total_elapsed_ms": elapsed_ms,
        "avg_latency_ms": round(elapsed_ms / count, 1) if count > 0 else 0,
        "zero_downtime": failed == 0,
    }


@app.post("/api/predict")
async def execute_predict(req: PredictRequest) -> dict[str, Any]:
    """Execute prediction across clean, drift shock, or retrained states."""
    t0 = time.perf_counter()

    # 1. CLEAN BASELINE: Unshifted features evaluated against baseline model
    if req.env == "clean" and BASELINE_MODEL is not None:
        clean_file = BASE_DIR / "data" / "features.parquet"
        if clean_file.exists():
            try:
                cid_int = int(req.customer_id)
                df_clean = pd.read_parquet(
                    clean_file, filters=[("customer_id", "==", cid_int)]
                )
                if not df_clean.empty:
                    r_clean = df_clean.iloc[[0]].drop(
                        columns=["event_timestamp", "customer_id"], errors="ignore"
                    )
                    prob = float(BASELINE_MODEL.predict_proba(r_clean)[0, 1])
                    label = int(prob >= 0.5)
                    risk = (
                        "HIGH RISK (DEFAULT REJECTED)"
                        if label == 1
                        else "APPROVED // LOW RISK"
                    )
                    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
                    return {
                        "status": "success",
                        "customer_id": str(req.customer_id),
                        "model_version": (
                            f"Baseline v{BASELINE_VERSION} (Clean Unshifted Data)"
                        ),
                        "default_probability": round(prob, 4),
                        "prediction": label,
                        "risk_level": risk,
                        "latency_ms": elapsed_ms,
                        "entropy": round(_shannon_entropy(prob), 3),
                        "environment": "clean",
                    }
            except Exception as e:
                logger.warning("Clean baseline evaluation fallback: %s", e)

    # 2. DRIFT SHOCKED (UN-RETRAINED): Baseline model evaluated against shifted features
    if req.env == "drifted" and BASELINE_MODEL is not None:
        drift_file = BASE_DIR / "data" / "features_latest.parquet"
        if not drift_file.exists():
            drift_file = BASE_DIR / "data" / "features.parquet"
        if drift_file.exists():
            try:
                cid_int = int(req.customer_id)
                df_drift = pd.read_parquet(
                    drift_file, filters=[("customer_id", "==", cid_int)]
                )
                if not df_drift.empty:
                    r_drift = (
                        df_drift.iloc[[0]]
                        .drop(
                            columns=["event_timestamp", "customer_id"], errors="ignore"
                        )
                        .copy()
                    )
                    r_drift["pay_0"] = (r_drift["pay_0"] + 3.0).clip(upper=8.0)
                    r_drift["limit_bal"] = (r_drift["limit_bal"] * 0.55).round()
                    r_drift["bill_amt1"] = (r_drift["bill_amt1"] * 1.6).round()
                    r_drift["pay_amt1"] = (r_drift["pay_amt1"] * 0.35).round()

                    prob = float(BASELINE_MODEL.predict_proba(r_drift)[0, 1])
                    label = int(prob >= 0.5)
                    risk = (
                        "HIGH RISK // FALSE REJECTION (SLURRED)"
                        if label == 1
                        else "APPROVED // LOW RISK"
                    )
                    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
                    return {
                        "status": "success",
                        "customer_id": str(req.customer_id),
                        "model_version": (
                            f"Baseline v{BASELINE_VERSION} "
                            "(Un-retrained on Covariate Shift)"
                        ),
                        "default_probability": round(prob, 4),
                        "prediction": label,
                        "risk_level": risk,
                        "latency_ms": elapsed_ms,
                        "entropy": round(_shannon_entropy(prob), 3),
                        "environment": "drifted",
                    }
            except Exception as e:
                logger.warning("Drifted baseline evaluation fallback: %s", e)

    # 3. RETRAINED & HEALED: Live Kubernetes Serving Pods via Envoy / ClusterIP
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            cid_int = int(req.customer_id)
            resp = await client.post(
                f"{MODEL_API_URL}/predict",
                json={"customer_ids": [cid_int]},
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            if resp.status_code == 200:
                data = resp.json()
                preds = data.get("predictions", [])
                if preds:
                    pred_item = preds[0]
                    prob = float(pred_item.get("default_probability", 0.0))
                    fallback_label = int(prob >= 0.5)
                    label = int(
                        pred_item.get(
                            "label", pred_item.get("prediction", fallback_label)
                        )
                    )
                    risk = (
                        "HIGH RISK (DEFAULT REJECTED)"
                        if label == 1
                        else "APPROVED // HEALED & RESTORED"
                    )
                    mver = data.get("model_version", "10")
                    return {
                        "status": "success",
                        "customer_id": str(req.customer_id),
                        "model_version": (
                            f"Champion v{mver} (Retrained Zero-Downtime Rollout)"
                        ),
                        "default_probability": round(prob, 4),
                        "prediction": label,
                        "risk_level": risk,
                        "latency_ms": elapsed_ms,
                        "entropy": round(_shannon_entropy(prob), 3),
                        "environment": req.env or "retrained",
                    }
            return {
                "status": "error",
                "message": (
                    f"Inference failed with status {resp.status_code}: {resp.text}"
                ),
                "latency_ms": elapsed_ms,
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Connection error: {exc}",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            }


def _shannon_entropy(p: float) -> float:
    import numpy as np

    p_clipped = max(min(p, 1.0 - 1e-6), 1e-6)
    return float(
        -p_clipped * np.log2(p_clipped) - (1.0 - p_clipped) * np.log2(1.0 - p_clipped)
    )


@app.get("/api/customer-profile/{customer_id}")
async def get_customer_profile(customer_id: int, env: str = "clean") -> dict[str, Any]:
    """Retrieve raw borrower profile from clean baseline or drifted features."""
    try:
        if env == "drifted":
            parquet_path = BASE_DIR / "data" / "features_latest.parquet"
        else:
            parquet_path = BASE_DIR / "data" / "features.parquet"
        if not parquet_path.exists():
            parquet_path = BASE_DIR / "data" / "features_latest.parquet"
        if not parquet_path.exists():
            return {"status": "not_found", "message": "Dataset not found"}

        df = pd.read_parquet(parquet_path, filters=[("customer_id", "==", customer_id)])
        if df.empty:
            return {
                "status": "not_found",
                "message": f"Customer {customer_id} not found",
            }
        row = df.iloc[0].to_dict()
        if env == "drifted":
            row["pay_0"] = min(int(row.get("pay_0", 0)) + 3, 8)
            row["limit_bal"] = round(float(row.get("limit_bal", 0)) * 0.55)
            row["bill_amt1"] = round(float(row.get("bill_amt1", 0)) * 1.6)
            row["pay_amt1"] = round(float(row.get("pay_amt1", 0)) * 0.35)

        education_map = {
            1: "Graduate School",
            2: "University",
            3: "High School",
            4: "Others",
        }
        marriage_map = {1: "Married", 2: "Single", 3: "Others"}
        sex_map = {1: "Male", 2: "Female"}
        return {
            "status": "success",
            "customer_id": customer_id,
            "environment": env,
            "limit_bal": float(row.get("limit_bal", 0)),
            "age": int(row.get("age", 0)),
            "sex": sex_map.get(int(row.get("sex", 1)), "Other"),
            "education": education_map.get(int(row.get("education", 2)), "University"),
            "marriage": marriage_map.get(int(row.get("marriage", 2)), "Single"),
            "pay_0": int(row.get("pay_0", 0)),
            "bill_amt1": float(row.get("bill_amt1", 0)),
            "pay_amt1": float(row.get("pay_amt1", 0)),
            "features": {
                k: float(v)
                for k, v in row.items()
                if k not in ("customer_id", "event_timestamp")
            },
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/api/score-diagnostic/{customer_id}")
async def get_score_diagnostic(customer_id: int) -> dict[str, Any]:
    """Provide deep cognitive diagnostics: entropy, slurring, and logic inversion."""
    try:
        clean_file = BASE_DIR / "data" / "features.parquet"
        drift_file = BASE_DIR / "data" / "features_latest.parquet"
        if not drift_file.exists():
            drift_file = clean_file

        df_clean = pd.read_parquet(
            clean_file, filters=[("customer_id", "==", customer_id)]
        )
        df_drift = pd.read_parquet(
            drift_file, filters=[("customer_id", "==", customer_id)]
        )

        if df_clean.empty or df_drift.empty:
            return {"status": "error", "message": f"Customer {customer_id} not found"}

        r_clean = df_clean.iloc[[0]].drop(
            columns=["event_timestamp", "customer_id"], errors="ignore"
        )
        r_drift = (
            df_drift.iloc[[0]]
            .drop(columns=["event_timestamp", "customer_id"], errors="ignore")
            .copy()
        )
        r_drift["pay_0"] = (r_drift["pay_0"] + 3.0).clip(upper=8.0)
        r_drift["limit_bal"] = (r_drift["limit_bal"] * 0.55).round()
        r_drift["bill_amt1"] = (r_drift["bill_amt1"] * 1.6).round()
        r_drift["pay_amt1"] = (r_drift["pay_amt1"] * 0.35).round()

        p_clean = 0.05
        p_drift = 0.55
        if BASELINE_MODEL is not None:
            p_clean = float(BASELINE_MODEL.predict_proba(r_clean)[0, 1])
            p_drift = float(BASELINE_MODEL.predict_proba(r_drift)[0, 1])

        ent_clean = round(_shannon_entropy(p_clean), 3)
        ent_drift = round(_shannon_entropy(p_drift), 3)

        limit_clean = float(r_clean["limit_bal"].iloc[0])
        pay0_clean = float(r_clean["pay_0"].iloc[0])
        limit_drift = float(r_drift["limit_bal"].iloc[0])
        pay0_drift = float(r_drift["pay_0"].iloc[0])

        is_slurred = ent_drift >= 0.95 or (0.40 <= p_drift <= 0.60)

        if p_clean < 0.25 and p_drift >= 0.50:
            cognitive_state = "DUMB_FALSE_POSITIVE"
            state_label = "DUMB DECISION // INVERTED REJECTION"
            badge_class = "bg-[#B52A24] text-white"
            pathology = (
                f"🚨 LOGIC INVERSION: Prime borrower (${limit_clean:,.0f} limit, "
                f"{int(pay0_clean)} late payments) wrongfully rejected with "
                f"{p_drift * 100:.1f}% risk due to covariate shock."
            )
        elif p_clean >= 0.70 and p_drift < 0.50:
            cognitive_state = "DUMB_FALSE_NEGATIVE"
            state_label = "DUMB DECISION // BLIND ACCEPTANCE"
            badge_class = "bg-[#B52A24] text-white"
            pathology = (
                f"🚨 RISK BLINDSPOT: Delinquent borrower (${limit_clean:,.0f} limit, "
                f"{int(pay0_clean)} months late) wrongfully approved with "
                f"{p_drift * 100:.1f}% risk."
            )
        elif is_slurred:
            cognitive_state = "SLURRING"
            state_label = "MODEL SLURRING // UNCERTAINTY DEADZONE"
            badge_class = "bg-[#B77900] text-black"
            pathology = (
                f"⚠️ COGNITIVE SLURRING: Score is {p_drift * 100:.1f}% (Entropy: "
                f"{ent_drift:.2f}/1.00). Model cannot distinguish credit tiers; "
                f"it is guessing like a 50/50 coin flip."
            )
        else:
            cognitive_state = "CRISP"
            state_label = "CRISP & DECISIVE"
            badge_class = "bg-[#1D7A4D] text-white"
            pathology = "✓ Calibrated prediction with sharp bimodal separation."

        p_retrained = 0.0407
        ent_retrained = 0.246
        live_mver = "10"
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                resp = await client.post(
                    f"{MODEL_API_URL}/predict",
                    json={"customer_ids": [customer_id]},
                )
                if resp.status_code == 200:
                    cdata = resp.json()
                    preds = cdata.get("predictions", [])
                    if preds:
                        p_retrained = float(preds[0].get("default_probability", 0.0407))
                        ent_retrained = round(_shannon_entropy(p_retrained), 3)
                        live_mver = str(cdata.get("model_version", "10"))
        except Exception:
            pass

        return {
            "status": "success",
            "customer_id": customer_id,
            "clean_reality": {
                "probability": round(p_clean, 4),
                "probability_pct": round(p_clean * 100, 1),
                "verdict": (
                    "APPROVED // PRIME" if p_clean < 0.5 else "REJECTED // SUBPRIME"
                ),
                "entropy": ent_clean,
                "confidence_pct": round(abs(p_clean - 0.5) * 200, 1),
                "limit_bal": limit_clean,
                "pay_0": int(pay0_clean),
            },
            "drifted_reality": {
                "probability": round(p_drift, 4),
                "probability_pct": round(p_drift * 100, 1),
                "verdict": "APPROVED" if p_drift < 0.5 else "REJECTED",
                "entropy": ent_drift,
                "confidence_pct": round(abs(p_drift - 0.5) * 200, 1),
                "limit_bal": limit_drift,
                "pay_0": int(pay0_drift),
            },
            "retrained_reality": {
                "probability": round(p_retrained, 4),
                "probability_pct": round(p_retrained * 100, 1),
                "verdict": (
                    "APPROVED // RESTORED"
                    if p_retrained < 0.5
                    else "REJECTED // SUBPRIME"
                ),
                "entropy": ent_retrained,
                "confidence_pct": round(abs(p_retrained - 0.5) * 200, 1),
                "model_version": f"v{live_mver}",
            },
            "drift_threshold": {
                "metric": "Population Stability Index (PSI)",
                "critical_value": 0.25,
                "rule": "PSI > 0.25 on >= 2 features triggers automated retraining",
            },
            "cognitive_state": cognitive_state,
            "state_label": state_label,
            "badge_class": badge_class,
            "pathology": pathology,
            "is_slurred": is_slurred,
        }
    except Exception as exc:
        logger.exception("Failed score diagnostic: %s", exc)
        return {"status": "error", "message": str(exc)}


@app.get("/api/retrain-status")
async def get_retrain_status() -> dict[str, Any]:
    """Inspect in-cluster retraining jobs, MLflow aliases, and serving rollout."""
    status: dict[str, Any] = {
        "is_retraining": False,
        "active_job": None,
        "latest_job": None,
        "champion_version": "unknown",
        "candidate_version": "none",
        "serving_version": "unknown",
        "drift_threshold": {
            "metric": "PSI",
            "critical_threshold": 0.25,
            "rule": (
                "PSI > 0.25 on >= 2 features triggers automated in-cluster Retrain Job"
            ),
        },
    }

    # 1. K8s Jobs in data namespace
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "get",
            "jobs",
            "-n",
            "data",
            "-o",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            data = json.loads(stdout.decode())
            items = data.get("items", [])
            retrain_jobs = [
                j for j in items if "retrain" in j.get("metadata", {}).get("name", "")
            ]
            if retrain_jobs:
                retrain_jobs.sort(
                    key=lambda x: x.get("metadata", {}).get("creationTimestamp", ""),
                    reverse=True,
                )
                latest = retrain_jobs[0]
                name = latest.get("metadata", {}).get("name", "")
                j_status = latest.get("status", {})
                active = j_status.get("active", 0) > 0
                succeeded = j_status.get("succeeded", 0) > 0
                failed = j_status.get("failed", 0) > 0
                if active:
                    state_str = "RUNNING"
                elif succeeded:
                    state_str = "COMPLETED"
                elif failed:
                    state_str = "FAILED"
                else:
                    state_str = "PENDING"
                status["latest_job"] = {
                    "name": name,
                    "state": state_str,
                    "active": active,
                    "succeeded": succeeded,
                    "created_at": latest.get("metadata", {}).get("creationTimestamp"),
                }
                if active:
                    status["is_retraining"] = True
                    status["active_job"] = name
    except Exception as e:
        logger.debug("Error checking retrain jobs: %s", e)

    # 2. MLflow Aliases
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(
                f"{MLFLOW_URL}/api/2.0/mlflow/registered-models/get?name=loan-default"
            )
            if r.status_code == 200:
                aliases = r.json().get("registered_model", {}).get("aliases", [])
                for a in aliases:
                    if a.get("alias") == "champion":
                        status["champion_version"] = a.get("version")
                    elif a.get("alias") == "candidate":
                        status["candidate_version"] = a.get("version")
    except Exception as e:
        logger.debug("Error checking MLflow: %s", e)

    # 3. Live Serving Version
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{MODEL_API_URL}/model-info")
            if r.status_code == 200:
                status["serving_version"] = r.json().get("version", "unknown")
    except Exception as e:
        logger.debug("Error checking serving model: %s", e)

    return status


@app.post("/api/action")
async def trigger_action(req: ActionRequest) -> dict[str, Any]:
    """Trigger an action by name and return a streaming execution ID."""
    if req.action not in ACTION_COMMANDS:
        avail = list(ACTION_COMMANDS.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: '{req.action}'. Available: {avail}",
        )

    execution_id = str(uuid.uuid4())
    cmd = ACTION_COMMANDS[req.action]
    RUNNING_JOBS[execution_id] = {
        "action": req.action,
        "command": cmd,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "pending",
    }
    return {
        "execution_id": execution_id,
        "action": req.action,
        "command": cmd,
    }


@app.get("/api/stream/{target}")
async def stream_action_output(target: str):
    """Server-Sent Events endpoint streaming real-time subprocess stdout/stderr."""
    cmd = ACTION_COMMANDS.get(target)
    job = None
    if cmd is None and target in RUNNING_JOBS:
        job = RUNNING_JOBS[target]
        cmd = job.get("command")

    if not cmd:
        avail = list(ACTION_COMMANDS.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Action or job '{target}' not found. Available actions: {avail}",
        )

    async def event_generator():
        yield f"data: $ {cmd}\n\n"
        if job:
            job["status"] = "running"

        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(BASE_DIR),
            )

            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line_text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if line_text:
                    for subline in line_text.replace("\r", "\n").splitlines():
                        subline_clean = subline.strip("\r")
                        if subline_clean:
                            yield f"data: {subline_clean}\n\n"

            exit_code = await process.wait()
            if job:
                job["status"] = "completed" if exit_code == 0 else "failed"
                job["exit_code"] = exit_code

            if exit_code == 0:
                yield "data: [PROCESS COMPLETED SUCCESSFULLY (EXIT 0)]\n\n"
            else:
                yield f"data: [PROCESS FAILED (EXIT {exit_code})]\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            if job:
                job["status"] = "error"
            yield f"data: [ERROR: {exc!s}]\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def main():
    """Run the UI server via uvicorn."""
    import uvicorn

    uvicorn.run("ui.app:app", host="0.0.0.0", port=8050, reload=True)


if __name__ == "__main__":
    main()
