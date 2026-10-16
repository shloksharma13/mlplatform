#!/usr/bin/env bash
# Train via Feast + MLflow in-cluster and register the new model version.
# Usage: TRIGGER=manual|drift [SHUFFLE=1] bash scripts/train_mlflow.sh
# SHUFFLE=1 permutes labels first (drill-only: AUC~=0.5 candidate for P7.4b).
# Idempotent: the previous train-mlflow Job is deleted before a new one runs.
set -Eeuo pipefail

readonly TRIGGER="${TRIGGER:-manual}"
readonly GIT_SHA="$(git rev-parse HEAD)"
readonly RENDERED="/tmp/train-job.rendered.yaml"
SHUFFLE_FLAG=""
if [[ "${SHUFFLE:-0}" == "1" ]]; then
  SHUFFLE_FLAG="--shuffle-labels"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRAIN_YAML="${REPO_ROOT}/infra/jobs/train-job.yaml"
RUN_JOB_SCRIPT="${REPO_ROOT}/scripts/run_job.sh"

sed -e "s|localhost:5001/training:dev|${TRAINING_IMAGE:-localhost:5001/training:dev}|g" \
  -e "s/__TRIGGER__/${TRIGGER}/" -e "s/__GIT_SHA__/${GIT_SHA}/" \
  -e "s/__SHUFFLE_FLAG__/${SHUFFLE_FLAG}/" \
  "${TRAIN_YAML}" >"${RENDERED}"
JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-1200}" bash "${RUN_JOB_SCRIPT}" \
  "${RENDERED}" data
