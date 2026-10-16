#!/usr/bin/env bash
# Post-apply bootstrap for the local Terraform stack.
# Merges the kind cluster kubeconfig into ~/.kube/config and waits until
# every data-layer workload is ready. Safe to rerun.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

echo "reading kubeconfig path from terraform outputs"
KUBECONFIG_REL="$(terraform -chdir=infra/envs/local output -raw kubeconfig)"
case "${KUBECONFIG_REL}" in
  /*) KUBECONFIG_FILE="${KUBECONFIG_REL}" ;;
  *) KUBECONFIG_FILE="infra/envs/local/${KUBECONFIG_REL}" ;;
esac
if [ ! -f "${KUBECONFIG_FILE}" ]; then
  echo "ERROR: kubeconfig file not found at ${KUBECONFIG_FILE}" >&2
  exit 1
fi

echo "merging ${KUBECONFIG_FILE} into ~/.kube/config"
mkdir -p "${HOME}/.kube"
touch "${HOME}/.kube/config"
KUBECONFIG="${KUBECONFIG_FILE}:${HOME}/.kube/config" \
  kubectl config view --flatten >"${HOME}/.kube/config.merged"
mv "${HOME}/.kube/config.merged" "${HOME}/.kube/config"
kubectl config use-context kind-mlp

echo "waiting for nodes to be Ready"
kubectl wait --for=condition=Ready nodes --all --timeout=300s

echo "waiting for data-layer rollouts"
kubectl rollout status statefulset/postgres -n data --timeout=600s
kubectl rollout status deployment/minio -n data --timeout=600s
kubectl rollout status deployment/redis -n data --timeout=600s

echo "waiting for init/bucket jobs to complete"
# Hook jobs are deleted after success (hook-succeeded policy): a missing job
# means it already completed. Guard with the release status instead.
for job in postgres-init minio-buckets; do
  if kubectl get "job/${job}" -n data >/dev/null 2>&1; then
    kubectl wait --for=condition=complete "job/${job}" \
      -n data --timeout=600s
  else
    echo "job ${job} already cleaned up (hook succeeded)"
  fi
done
for release in postgres minio redis; do
  status="$(helm status "${release}" -n data -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["status"])')"
  if [ "${status}" != "deployed" ]; then
    echo "ERROR: release ${release} status is ${status}" >&2
    exit 1
  fi
  echo "release ${release}: ${status}"
done

echo "waiting for metrics-server"
kubectl rollout status deployment/metrics-server -n kube-system --timeout=300s

echo "--- pods ---"
kubectl get pods -A
echo "--- contract outputs ---"
terraform -chdir=infra/envs/local output
echo "bootstrap complete"
