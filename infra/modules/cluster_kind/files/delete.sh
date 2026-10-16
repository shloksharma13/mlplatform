#!/usr/bin/env bash
# Destroy-time companion of create.sh: remove the cluster and the files this
# module generated. Never fails (keeps `make down` idempotent).
set -Eeuo pipefail

: "${CLUSTER_NAME:?}"

kind delete cluster --name "${CLUSTER_NAME}" || true
rm -f "${KIND_CONFIG:-}" "${KUBECONFIG_FILE:-}" || true
echo "cluster ${CLUSTER_NAME} deleted"
