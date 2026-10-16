#!/usr/bin/env bash
# Release one MLflow model version to production (P7.3, local equivalent of
# model-release.yml): gate -> staging -> loadcheck -> promote -> prod deploy
# -> verify, with rejection or rollback on every failure path.
# Usage: bash scripts/model_release_local.sh <VERSION> [IMAGE_TAG]
set -Eeuo pipefail

readonly VERSION="${1:?model version required}"
readonly IMAGE_TAG="${2:-dev}"
readonly MLFLOW="${MLFLOW_TRACKING_URI:-http://localhost:5000}"
readonly MODEL="${MODEL_NAME:-loan-default}"
# Overridable for in-cluster execution (retrain Job): the runner uses host
# NodePorts, the cluster uses ClusterIP DNS names.
readonly STAGING_URL="${STAGING_URL:-http://localhost:8081}"
readonly PROD_URL="${PROD_URL:-http://localhost:8080}"

alias_version() {
  curl -s "${MLFLOW}/api/2.0/mlflow/registered-models/get?name=${MODEL}" |
    python3 -c "import json,sys; print('\n'.join(f\"{a['alias']}={a['version']}\" for a in json.load(sys.stdin)['registered_model'].get('aliases', [])))"
}
set_alias() {
  curl -s -X POST "${MLFLOW}/api/2.0/mlflow/registered-models/alias" \
    -H 'content-type: application/json' \
    -d "{\"name\":\"${MODEL}\",\"alias\":\"$1\",\"version\":\"$2\"}" >/dev/null
}
delete_alias() {
  curl -s -X DELETE "${MLFLOW}/api/2.0/mlflow/registered-models/alias" \
    -H 'content-type: application/json' \
    -d "{\"name\":\"${MODEL}\",\"alias\":\"$1\"}" >/dev/null || true
}
set_tag() {
  curl -s -X POST "${MLFLOW}/api/2.0/mlflow/model-versions/set-tag" \
    -H 'content-type: application/json' \
    -d "{\"name\":\"${MODEL}\",\"version\":\"$1\",\"key\":\"$2\",\"value\":\"$3\"}" >/dev/null
}
champion_of() {
  echo "$1" | grep '^champion=' | cut -d= -f2 || true
}

echo "=== model release: ${MODEL} version ${VERSION} (image ${IMAGE_TAG}) ==="
PREV_CHAMPION="$(champion_of "$(alias_version)")"
echo "previous champion: ${PREV_CHAMPION:-none}"

reject_version() {
  echo "REJECT version ${VERSION}: $1"
  set_tag "${VERSION}" gate_status rejected
  delete_alias candidate
  exit 1
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="${REPO_ROOT}/scripts"

echo "--- step 1: gate ---"
if ! env VERSION="${VERSION}" bash "${SCRIPTS_DIR}/run_gate.sh"; then
  reject_version "gate failed"
fi

echo "--- step 2: staging deploy ---"
if ! bash "${SCRIPTS_DIR}/deploy.sh" model-api-staging staging "${IMAGE_TAG}" \
  --set model.version="${VERSION}" --set service.nodePort=30081; then
  helm rollback model-api-staging -n staging || true
  reject_version "staging deploy failed"
fi

echo "--- step 3: staging loadcheck ---"
if ! python3 "${SCRIPTS_DIR}/loadcheck.py" --url "${STAGING_URL}" --total "${LOADCHECK_TOTAL:-2000}" --rps "${LOADCHECK_RPS:-20}"; then
  helm rollback model-api-staging -n staging || true
  reject_version "staging loadcheck failed"
fi

echo "--- step 4: promote champion ${PREV_CHAMPION:-none} -> ${VERSION} ---"
if [[ -n "${PREV_CHAMPION}" && "${PREV_CHAMPION}" != "${VERSION}" ]]; then
  delete_alias champion
fi
set_alias champion "${VERSION}"

rollback_prod() {
  echo "ROLLBACK: $1"
  helm rollback model-api -n serving || true
  if [[ -n "${PREV_CHAMPION}" && "${PREV_CHAMPION}" != "${VERSION}" ]]; then
    delete_alias champion
    set_alias champion "${PREV_CHAMPION}"
  fi
  set_tag "${VERSION}" gate_status rejected
  exit 1
}

echo "--- step 5: prod deploy ---"
if ! bash "${SCRIPTS_DIR}/deploy.sh" model-api serving "${IMAGE_TAG}" \
  --set model.version="${VERSION}"; then
  rollback_prod "prod deploy failed"
fi

echo "--- step 6: verify all pods serve ${VERSION} ---"
for _ in $(seq 1 24); do
  INFO="$(curl -s "${PROD_URL}/model-info" || true)"
  if echo "${INFO}" | grep -q "\"version\":\"${VERSION}\""; then
    break
  fi
  sleep 5
done
INFO="$(curl -s "${PROD_URL}/model-info")"
echo "${INFO}"
echo "${INFO}" | grep -q "\"version\":\"${VERSION}\"" ||
  rollback_prod "model-info does not report ${VERSION}"

echo "--- step 7: prod 100-request check ---"
python3 "${SCRIPTS_DIR}/loadcheck.py" --url "${PROD_URL}" --total 100 --rps 10 ||
  rollback_prod "prod verification requests failed"

echo "=== RELEASE COMPLETE ==="
echo "model=${MODEL} version=${VERSION} previous_champion=${PREV_CHAMPION:-none}"
echo "${GATE_REPORT:-gate_report.json}:"
cat "${GATE_REPORT:-gate_report.json}"
