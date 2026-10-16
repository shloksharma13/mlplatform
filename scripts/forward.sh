#!/usr/bin/env bash
# (Re)start kubectl port-forwards for the data services.
# Used when NodePorts are unreachable; safe to rerun (stale forwards are
# killed first). Forwards run in the background; logs go to /tmp/fwd-*.log.
set -Eeuo pipefail

pkill -f "kubectl port-forward -n data" 2>/dev/null || true
sleep 1

nohup kubectl port-forward -n data svc/postgres 5432:5432 \
  >/tmp/fwd-postgres.log 2>&1 &
nohup kubectl port-forward -n data svc/minio 9000:9000 9001:9001 \
  >/tmp/fwd-minio.log 2>&1 &
nohup kubectl port-forward -n data svc/redis 6379:6379 \
  >/tmp/fwd-redis.log 2>&1 &
sleep 5

echo "--- forward status ---"
curl -s -o /dev/null -w "minio api (localhost:9000): %{http_code}\n" \
  http://localhost:9000/minio/health/ready || true
PGPASSWORD="$(kubectl get secret -n data postgres-auth \
  -o jsonpath='{.data.postgres-password}' | base64 -d)" \
  kubectl exec -n data postgres-0 -- \
  psql -U mlp -d mlp -tc "SELECT count(*) FROM prediction_log;" \
  && echo "postgres reachable via in-cluster psql"
echo "port-forwards running (logs: /tmp/fwd-*.log)"
