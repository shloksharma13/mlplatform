#!/usr/bin/env bash
# Recreate an idempotent Job and fail promptly with logs on terminal failure.
set -Eeuo pipefail
command -v kubectl >/dev/null || { echo "ERROR: kubectl not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "ERROR: jq not found" >&2; exit 1; }
manifest="${1:?job manifest required}"
namespace="${2:-data}"
if [[ -n "${TRAINING_IMAGE:-}" ]]; then
  rendered_manifest="$(mktemp)"
  python3 - "${manifest}" "${rendered_manifest}" "${TRAINING_IMAGE}" <<'PY'
from pathlib import Path
import sys

source_path, output_path, image = sys.argv[1:]
source = Path(source_path).read_text()
local_image = "image: localhost:5001/training:dev"
cloud_image = f"image: {image}"
if local_image in source:
    source = source.replace(local_image, cloud_image)
elif cloud_image not in source:
    raise SystemExit(f"job manifest has no training image placeholder: {source_path}")
Path(output_path).write_text(source)
PY
  manifest="${rendered_manifest}"
  trap 'rm -f "${manifest}"' EXIT
fi
name="$(kubectl create --dry-run=client -f "$manifest" -o jsonpath='{.metadata.name}')"
kubectl -n "$namespace" delete job "$name" --ignore-not-found --wait=true
kubectl apply -f "$manifest"
deadline=$((SECONDS + ${JOB_TIMEOUT_SECONDS:-600}))
while ((SECONDS < deadline)); do
  status="$(kubectl -n "$namespace" get job "$name" -o json)"
  if jq -e '.status.conditions[]? | select(.type == "Complete" and .status == "True")' <<<"$status" >/dev/null; then
    kubectl -n "$namespace" logs "job/$name" --all-containers=true
    echo "job/$name completed"
    exit 0
  fi
  if jq -e '.status.conditions[]? | select(.type == "Failed" and .status == "True")' <<<"$status" >/dev/null; then
    break
  fi
  sleep 2
done
kubectl -n "$namespace" logs "job/$name" --all-containers=true --tail=100 || true
kubectl -n "$namespace" get pods -l "job-name=$name" -o wide
printf 'ERROR: job/%s failed or timed out\n' "$name" >&2
exit 1
