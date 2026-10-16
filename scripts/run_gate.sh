#!/usr/bin/env bash
# Score one candidate version through the release gate in-cluster.
# Usage: VERSION=2 bash scripts/run_gate.sh
# Prints the gate verdict and leaves the JSON report at
# ${GATE_REPORT_OUT:-gate_report.json}. Exit 0 on PASS, 1 on REJECT.
set -Eeuo pipefail

readonly VERSION="${VERSION:?candidate VERSION required (e.g. VERSION=2)}"
readonly RENDERED="/tmp/gate-job.rendered.yaml"
readonly REPORT_OUT="${GATE_REPORT_OUT:-gate_report.json}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE_YAML="${REPO_ROOT}/infra/jobs/gate-job.yaml"
RUN_JOB_SCRIPT="${REPO_ROOT}/scripts/run_job.sh"

sed -e "s/__CANDIDATE__/${VERSION}/" \
  -e "s|localhost:5001/training:dev|${TRAINING_IMAGE:-localhost:5001/training:dev}|g" \
  "${GATE_YAML}" >"${RENDERED}"
LOGS="$(mktemp)"
trap 'rm -f "${LOGS}"' EXIT
if JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-900}" bash "${RUN_JOB_SCRIPT}" \
  "${RENDERED}" data >"${LOGS}" 2>&1; then
  GATE_STATUS=0
else
  GATE_STATUS=1
fi
cat "${LOGS}"
python3 - "${LOGS}" <<'EOF'
import os
import re
import sys

logs = open(sys.argv[1]).read()
match = re.search(r"GATE verdict=(PASS|REJECT)", logs)
print(f"gate candidate verdict: {match.group(1) if match else 'UNKNOWN'}")
report = re.search(
    r"GATE_REPORT_BEGIN\n(\{.*?\n\})\nGATE_REPORT_END", logs, re.DOTALL
)
if report:
    out = os.environ.get("GATE_REPORT_OUT", "gate_report.json")
    open(out, "w").write(report.group(1) + "\n")
    print(f"{out} written")
EOF
exit "${GATE_STATUS}"
