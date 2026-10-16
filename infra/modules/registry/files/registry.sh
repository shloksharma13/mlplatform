#!/usr/bin/env bash
# Idempotent local registry bootstrap for kind (P2.1/P3.2).
# Runs a `registry:2` container on the host and attaches it to the `kind`
# network so cluster nodes pull `localhost:5001/*` via `kind-registry:5000`.
# Safe to rerun: existing containers are reused, never recreated.
set -Eeuo pipefail

: "${REGISTRY_NAME:=kind-registry}"
: "${REGISTRY_PORT:=5001}"
: "${REGISTRY_IMAGE:=registry:2}"

if [ "$(docker inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null || true)" != 'true' ]; then
  if docker inspect "${REGISTRY_NAME}" >/dev/null 2>&1; then
    docker rm -f "${REGISTRY_NAME}" >/dev/null
  fi
  docker run -d --restart=always \
    -p "127.0.0.1:${REGISTRY_PORT}:5000" \
    --network bridge --name "${REGISTRY_NAME}" \
    "${REGISTRY_IMAGE}" >/dev/null
  echo "started registry ${REGISTRY_NAME} on 127.0.0.1:${REGISTRY_PORT}"
else
  echo "registry ${REGISTRY_NAME} already running"
fi

if [ "$(docker inspect -f '{{json .NetworkSettings.Networks.kind}}' "${REGISTRY_NAME}" 2>/dev/null || true)" = 'null' ]; then
  docker network connect kind "${REGISTRY_NAME}" 2>/dev/null || true
  echo "attached ${REGISTRY_NAME} to kind network"
fi
