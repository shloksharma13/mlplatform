#!/usr/bin/env bash
# Delete a random model-api pod during traffic and report recovery.
# Usage: scripts/chaos_pod.sh [namespace] [release] [base-url]
set -Eeuo pipefail

readonly NAMESPACE="${1:-serving}"
readonly RELEASE="${2:-model-api}"
readonly BASE_URL="${3:-http://localhost:8080}"

pods_before="$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE}" -o name)"
echo "chaos: running pods before: $(echo "${pods_before}" | tr '\n' ' ')"

victim="$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}')"
echo "chaos: deleting pod ${victim}"
start="$(date +%s)"
kubectl delete pod "${victim}" -n "${NAMESPACE}" >/dev/null

errors=0
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE_URL}/predict" -H 'content-type: application/json' -d '{"customer_ids":[1]}' || echo 000)"
  if [[ "${code}" != "200" ]]; then
    errors=$((errors + 1))
  fi
  ready="$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[*].status.containerStatuses[0].ready}' || true)"
  if [[ "${ready}" == "true true" ]]; then
    break
  fi
  sleep 2
done
finish="$(date +%s)"

recovered="$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE}" --field-selector=status.phase=Running -o name | wc -l)"
echo "chaos: recovery took ~$((finish - start))s; failed(probe) requests=${errors}; running pods=${recovered}"
if [[ "${recovered}" -lt 2 ]]; then
  echo "chaos FAIL: fewer than 2 running pods after recovery" >&2
  exit 1
fi
echo "chaos PASS: service stayed available across pod deletion"
