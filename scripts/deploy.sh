#!/usr/bin/env bash
# Atomically release a chart, then prove it serves traffic.
# Usage: scripts/deploy.sh <release> <namespace> <image-tag> [extra helm args...]
set -Eeuo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <release> <namespace> <image-tag> [extra helm args...]" >&2
  exit 2
fi

readonly RELEASE="$1"
readonly NAMESPACE="$2"
readonly IMAGE_TAG="$3"
shift 3

echo "deploying ${RELEASE} into ${NAMESPACE} with image tag ${IMAGE_TAG}"

# Mirror the MinIO credentials (source of truth: terraform-managed secret in data)
# into the release namespace so the model-files init container can authenticate.
MINIO_USER="$(kubectl -n data get secret minio-auth -o jsonpath='{.data.root-user}' | base64 -d)"
MINIO_PASS="$(kubectl -n data get secret minio-auth -o jsonpath='{.data.root-password}' | base64 -d)"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "${NAMESPACE}" create secret generic minio-model \
  --from-literal=root-user="${MINIO_USER}" \
  --from-literal=root-password="${MINIO_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Mirror the Postgres app credentials (source of truth: terraform-managed secret in
# data) as a full SQLAlchemy URL so prediction rows reach prediction_log (P6.7).
PG_USER="$(kubectl -n data get secret postgres-auth -o jsonpath='{.data.username}' | base64 -d)"
PG_PASS="$(kubectl -n data get secret postgres-auth -o jsonpath='{.data.postgres-password}' | base64 -d)"
kubectl -n "${NAMESPACE}" create secret generic postgres-app \
  --from-literal=database-url="postgresql+psycopg://${PG_USER}:${PG_PASS}@postgres.data.svc:5432/mlp" \
  --dry-run=client -o yaml | kubectl apply -f -

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${REPO_ROOT}/infra/charts/model-api"
SMOKE_SCRIPT="${REPO_ROOT}/scripts/smoke.sh"

HELM_ARGS=()
if [[ "${DEPLOY_TARGET:-local}" == azure ]]; then
  HELM_ARGS+=(-f "${CHART_DIR}/values-azure.yaml")
  [[ "${NAMESPACE}" != staging ]] || HELM_ARGS+=(--set service.type=ClusterIP)
  if [[ -n "${PUBLIC_IP_NAME:-}" ]]; then
    annotations="$(python3 -c 'import json,os; print(json.dumps({"service.beta.kubernetes.io/azure-pip-name":os.environ["PUBLIC_IP_NAME"],"service.beta.kubernetes.io/azure-load-balancer-resource-group":os.environ["AZURE_RESOURCE_GROUP"]}))')"
    HELM_ARGS+=(--set-json "service.annotations=${annotations}")
  fi
  if [[ -n "${AZURE_ALLOWED_CIDR:-}" ]]; then
    ranges="$(python3 -c 'import json,os; print(json.dumps([x for x in [os.environ["AZURE_ALLOWED_CIDR"],os.environ.get("RUNNER_CIDR", "")] if x]))')"
    HELM_ARGS+=(--set-json "service.loadBalancerSourceRanges=${ranges}")
  fi
fi
if [[ -n "${MODEL_API_REPOSITORY:-}" ]]; then
  HELM_ARGS+=(--set "image.repository=${MODEL_API_REPOSITORY}")
fi

helm upgrade --install "${RELEASE}" "${CHART_DIR}" \
  --namespace "${NAMESPACE}" --create-namespace \
  --set "image.tag=${IMAGE_TAG}" \
  "${HELM_ARGS[@]}" \
  "$@" \
  --atomic --wait --timeout 5m

echo "release ${RELEASE} deployed; running smoke checks"
[[ "${SKIP_SMOKE:-0}" != 1 ]] || exit 0
if [[ "${RELEASE}" == "model-api-staging" ]]; then
  # The staging release serves host port 8081 (NodePort 30081); the default
  # smoke URL would silently test production instead. Overridable for
  # in-cluster release runs (ClusterIP DNS instead of host ports).
  bash "${SMOKE_SCRIPT}" "${NAMESPACE}" "${RELEASE}" "${STAGING_SMOKE_URL:-http://localhost:8081}"
else
  bash "${SMOKE_SCRIPT}" "${NAMESPACE}" "${RELEASE}" "${PROD_SMOKE_URL:-http://localhost:8080}"
fi
