#!/usr/bin/env bash
# Create (or recreate) the kind cluster from the rendered config, write a
# standalone kubeconfig, then re-attach the local registry (idempotent).
set -Eeuo pipefail

: "${CLUSTER_NAME:?}" "${KIND_CONFIG:?}" "${KUBECONFIG_FILE:?}" "${REGISTRY_SCRIPT:?}"

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "cluster ${CLUSTER_NAME} exists; deleting first so config changes apply"
  kind delete cluster --name "${CLUSTER_NAME}"
fi

kind create cluster --name "${CLUSTER_NAME}" \
  --config "${KIND_CONFIG}" \
  --kubeconfig "${KUBECONFIG_FILE}" \
  --wait 5m

echo "re-attaching the local registry"
KUBECONFIG="${KUBECONFIG_FILE}" bash "${REGISTRY_SCRIPT}"
echo "cluster ${CLUSTER_NAME} ready"
