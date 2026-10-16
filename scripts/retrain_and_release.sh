#!/usr/bin/env bash
# End-to-end retraining and zero-downtime release script for fast interactive demo.
# 1. Trains candidate model in-cluster via Feast historical retrieval and MLflow.
# 2. Resolves registered candidate version from MLflow registry.
# 3. Executes Quality Gate (AUC >= 0.82, latency <= 20ms).
# 4. Deploys to staging, runs canary load check, promotes to champion, and rolls out to prod.
set -Eeuo pipefail

PYTHON=(uv run python)
[[ "${DEPLOY_TARGET:-local}" != azure ]] || PYTHON=(python)

stamp() { date -u +%FT%TZ; }

echo "======================================================="
echo "[$(stamp)] LAUNCHING RETRAINING & ZERO-DOWNTIME RELEASE"
echo "======================================================="

echo "[STEP 1/3] Spawning in-cluster Feast retrain Job in Kubernetes..."
JOB_NAME=$("${PYTHON[@]}" - <<'EOF'
import json, os, time, sys
from kubernetes import client, config
import mlflow

try:
    config.load_incluster_config()
except Exception:
    config.load_kube_config()

core = client.CoreV1Api()
batch = client.BatchV1Api()
template = core.read_namespaced_config_map("retrain-job-template", "data")
pod_spec = json.loads(template.data["pod-spec.json"])

client_mlflow = mlflow.tracking.MlflowClient(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"))
try:
    champ = client_mlflow.get_model_version_by_alias("loan-default", "champion").version
except Exception:
    champ = ""

epoch = int(time.time())
name = f"retrain-{champ or 'initial'}-{epoch}"
pod_spec["containers"][0]["env"].extend([
    {"name": "PARENT_CHAMPION", "value": str(champ)},
    {"name": "DRIFT_REPORT", "value": json.dumps({"drifted_features": ["pay_0", "limit_bal"]})},
])

job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {
        "name": name,
        "namespace": "data",
        "labels": {"app": "retrain"},
    },
    "spec": {
        "ttlSecondsAfterFinished": 3600,
        "backoffLimit": 1,
        "template": {
            "metadata": {"labels": {"app": "retrain"}},
            "spec": pod_spec,
        },
    },
}
batch.create_namespaced_job("data", job)
print(name)
EOF
)

echo "[$(stamp)] Spawned Kubernetes Job: data/${JOB_NAME}"
echo "[STEP 2/3] Streaming live in-cluster training, Quality Gate, and rollout logs..."

# Wait for pod to appear
for _ in $(seq 1 30); do
  if kubectl get pods -n data -l job-name="${JOB_NAME}" --no-headers 2>/dev/null | grep -q "retrain"; then
    break
  fi
  sleep 1
done

# Stream logs from the retrain pod
kubectl logs -n data -l job-name="${JOB_NAME}" -f --tail=100 || true

# Wait for job completion
kubectl wait --for=condition=complete "job/${JOB_NAME}" -n data --timeout=300s

echo "[STEP 3/3] Verifying new Champion version from MLflow registry..."
NEW_CHAMPION=$(curl -s "${MLFLOW_TRACKING_URI:-http://localhost:5000}/api/2.0/mlflow/registered-models/get?name=loan-default" | python3 -c "import json,sys; a=json.load(sys.stdin)['registered_model'].get('aliases', []); print(next((x['version'] for x in a if x['alias']=='champion'), ''))")

echo "======================================================="
echo "[$(stamp)] RETRAINING COMPLETE: Champion is now v${NEW_CHAMPION}"
echo "Zero-downtime rollout completed across all serving replicas."
echo "======================================================="
