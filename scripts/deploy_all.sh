#!/usr/bin/env bash
# Deploy all platform workloads and demo baselines after cluster creation (P9.2).
# Idempotent: safe to run against a fresh cluster or an existing cluster.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

echo "=== 1. Building and pushing local service images ==="
docker build --file infra/Dockerfile --target model-api --tag localhost:5001/model-api:dev .
docker push localhost:5001/model-api:dev

docker build --file infra/Dockerfile --target training --tag localhost:5001/training:dev .
docker push localhost:5001/training:dev

docker build --file infra/Dockerfile --target mlflow --tag localhost:5001/mlflow:dev .
docker push localhost:5001/mlflow:dev

docker build --file infra/Dockerfile --target remediator --tag localhost:5001/remediator:dev .
docker push localhost:5001/remediator:dev

docker build --file infra/Dockerfile --target drift-monitor --tag localhost:5001/drift-monitor:dev .
docker push localhost:5001/drift-monitor:dev

docker build --file infra/Dockerfile --target retrain --tag localhost:5001/retrain:dev .
docker push localhost:5001/retrain:dev

echo "=== 2. Creating target namespaces and deploying remediator ==="
kubectl create namespace serving --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace staging --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install remediator infra/charts/remediator \
  --namespace observability --create-namespace --atomic --wait --timeout 5m

echo "=== 3. Seeding data and Feast feature store ==="
uv run python -m ml.data.generate --out data --seed 42
bash scripts/publish_feast_data.sh
bash scripts/run_job.sh infra/jobs/feast-apply-job.yaml data
bash scripts/run_job.sh infra/jobs/feast-materialize-job.yaml data
bash scripts/run_job.sh infra/jobs/feast-verify-job.yaml data

echo "=== 4. Training and releasing MLflow models ==="
export MLFLOW_TRACKING_URI="http://localhost:5000"
VERSIONS="$(curl -s "http://localhost:5000/api/2.0/mlflow/model-versions/search?filter=name%3D%27loan-default%27" | \
  python3 -c "import json,sys; print(len(json.load(sys.stdin).get('model_versions',[])))" 2>/dev/null || echo "0")"

if [[ "${VERSIONS}" -eq 0 ]]; then
  echo "training initial baseline champion v1..."
  TRIGGER=manual bash scripts/train_mlflow.sh
fi

VERSIONS="$(curl -s "http://localhost:5000/api/2.0/mlflow/model-versions/search?filter=name%3D%27loan-default%27" | \
  python3 -c "import json,sys; print(len(json.load(sys.stdin).get('model_versions',[])))" 2>/dev/null || echo "0")"

if [[ "${VERSIONS}" -eq 1 ]]; then
  echo "training baseline candidate v2..."
  TRIGGER=manual bash scripts/train_mlflow.sh
  echo "releasing v2 as champion..."
  bash scripts/model_release_local.sh 2 dev
else
  echo "=== 5. Deploying model-api ==="
  bash scripts/deploy.sh model-api serving dev
fi

echo "=== 6. Deploying drift monitor & retraining platform ==="
bash scripts/deploy_monitor.sh

echo "=== 7. Resetting demo baseline ==="
uv run python scripts/reset_demo.py

echo "=== deploy-all complete ==="
