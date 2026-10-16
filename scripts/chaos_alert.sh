#!/usr/bin/env bash
# P5.7 — end-to-end alert drill: latency chaos -> HighP95Latency fires ->
# remediator scales the HPA floor + annotates Grafana -> reset.
# Prints a timeline; exits non-zero if the alert never fires or no action occurs.
set -Eeuo pipefail

readonly NAMESPACE="${NAMESPACE:-serving}"
readonly LATENCY_MS="${LATENCY_MS:-500}"
readonly RPS="${RPS:-10}"
readonly WAIT_SECONDS="${WAIT_SECONDS:-240}"

echo "[0s] baseline HPA"
BASE_MIN="$(kubectl -n "${NAMESPACE}" get hpa model-api -o jsonpath='{.spec.minReplicas}')"
echo "  hpa minReplicas=${BASE_MIN}"

echo "[0s] enabling ${LATENCY_MS}ms latency on all model-api pods"
for pod in $(kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/name=model-api -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'); do
  kubectl -n "${NAMESPACE}" exec "${pod}" -c api -- \
    python -c "import httpx; print(httpx.post('http://localhost:8080/debug/latency?ms=${LATENCY_MS}', timeout=10).json())"
done

echo "[0s] starting traffic (${RPS} rps) in background"
source .venv/bin/activate 2>/dev/null || true
python scripts/traffic.py --rps "${RPS}" --duration "${WAIT_SECONDS}" --url "${MODEL_API_URL:-http://localhost:8080}" --max-error-rate 1.0 > /tmp/chaos-traffic.log 2>&1 &
TRAFFIC_PID=$!

fired_at=""
acted_at=""
for elapsed in $(seq 15 15 "${WAIT_SECONDS}"); do
  sleep 15
  # Has the alert fired? (ALERTS==1 with alertstate=firing; pending->firing ~2-3 min)
  state="$(curl -s -G "${PROMETHEUS_URL:-http://localhost:9090}/api/v1/query" --data-urlencode 'query=ALERTS{alertname="HighP95Latency"}' | python3 -c 'import json,sys; d=json.load(sys.stdin); r=d["data"]["result"]; print(r[0]["value"][1] + ":" + r[0]["metric"].get("alertstate","?") if r else "none")' 2>/dev/null || echo "query-failed")"
  current_min="$(kubectl -n "${NAMESPACE}" get hpa model-api -o jsonpath='{.spec.minReplicas}')"
  echo "[${elapsed}s] ALERTS=${state} hpa_min=${current_min}"
  if [[ "${state}" == "1:firing" && -z "${fired_at}" ]]; then
    fired_at="${elapsed}s"
    echo "  >>> HighP95Latency FIRING"
  fi
  if [[ "${current_min}" != "${BASE_MIN}" && -z "${acted_at}" ]]; then
    acted_at="${elapsed}s"
    echo "  >>> remediator raised HPA floor ${BASE_MIN}->${current_min}"
  fi
  if [[ -n "${fired_at}" && -n "${acted_at}" ]]; then
    break
  fi
done

wait "${TRAFFIC_PID}" || true
tail -n 3 /tmp/chaos-traffic.log

echo "resetting latency to 0 on all pods"
for pod in $(kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/name=model-api -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'); do
  kubectl -n "${NAMESPACE}" exec "${pod}" -c api -- \
    python -c "import httpx; print(httpx.post('http://localhost:8080/debug/latency?ms=0', timeout=10).json())"
done

echo "restoring HPA floor to ${BASE_MIN} (drill cleanup)"
kubectl -n "${NAMESPACE}" patch hpa model-api --type merge -p "{\"spec\":{\"minReplicas\":${BASE_MIN}}}" >/dev/null

echo "=== drill timeline ==="
echo "alert fired: ${fired_at:-NEVER}"
echo "remediator acted: ${acted_at:-NEVER}"
if [[ -z "${fired_at}" || -z "${acted_at}" ]]; then
  echo "drill FAIL" >&2
  exit 1
fi
echo "drill PASS"
