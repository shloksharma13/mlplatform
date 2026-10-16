#!/usr/bin/env bash
# Build and deploy the drift monitor plus retraining platform (P8.2-P8.4).
# Images are built first so helm never installs a missing tag. The database
# secret is mirrored from the terraform-managed postgres-auth source of truth.
set -Eeuo pipefail

echo "building drift-monitor image"
hadolint infra/Dockerfile
docker build --file infra/Dockerfile --target drift-monitor \
  --tag localhost:5001/drift-monitor:dev .
docker push localhost:5001/drift-monitor:dev

echo "building retrain image"
hadolint infra/Dockerfile
docker build --file infra/Dockerfile --target retrain \
  --tag localhost:5001/retrain:dev .
docker push localhost:5001/retrain:dev

PG_USER="$(kubectl -n data get secret postgres-auth -o jsonpath='{.data.username}' | base64 -d)"
PG_PASS="$(kubectl -n data get secret postgres-auth -o jsonpath='{.data.postgres-password}' | base64 -d)"
kubectl -n observability create secret generic drift-monitor-db \
  --from-literal=database-url="postgresql+psycopg://${PG_USER}:${PG_PASS}@postgres.data.svc:5432/mlp" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install retrain infra/charts/retrain \
  --namespace data --create-namespace --atomic --wait --timeout 5m
helm upgrade --install drift-monitor infra/charts/drift-monitor \
  --namespace observability --create-namespace --atomic --wait --timeout 5m

# The local registry deliberately reuses the `dev` tag.  Force a new pod so
# an idempotent rerun actually picks up the image just built and pushed.
kubectl -n observability rollout restart deployment/drift-monitor

echo "drift-monitor + retrain deployed; checking /state"
kubectl -n observability rollout status deploy/drift-monitor --timeout=120s
kubectl -n observability port-forward deploy/drift-monitor 8089:8080 >/tmp/pf-drift.log 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 4
curl -s localhost:8089/healthz
echo
for _ in $(seq 1 30); do
  STATE="$(curl -sf localhost:8089/state || true)"
  if echo "${STATE}" | grep -q '"champion_version":"'; then
    echo "${STATE}" | head -c 600
    echo
    exit 0
  fi
  sleep 2
done
echo "ERROR: drift monitor did not complete its first evaluation" >&2
cat /tmp/pf-drift.log >&2 || true
exit 1
