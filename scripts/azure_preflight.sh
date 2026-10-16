#!/usr/bin/env bash
# Preflight for the Azure move: read-only checks, creates nothing billable.
# Run BEFORE any apply: bash scripts/azure_preflight.sh
# Fails fast on: missing CLIs, no az login, unregistered providers, SKU blocked
# for the subscription/region (NotAvailableForSubscription), or insufficient
# Total Regional vCPU quota. Student subscriptions cannot request quota
# increases, so every failure prints the remediation (region/SKU switch).
set -Eeuo pipefail

LOCATION="${AZURE_LOCATION:-indiasouthcentral}"
VM_SIZE="${AZURE_VM_SIZE:-Standard_D4as_v4}"
NODE_COUNT="${AZURE_NODE_COUNT:-1}"

need() { command -v "$1" >/dev/null || { echo "MISSING: $1 not installed" >&2; exit 1; }; }
need az
need kubectl
need helm
need terraform
need docker

echo "== azure login =="
SUBSCRIPTION="$(az account show --query id -o tsv 2>/dev/null || true)"
[[ -n "${SUBSCRIPTION}" ]] || {
  echo "FAIL: not logged in. Run: az login" >&2
  exit 1
}
echo "subscription: ${SUBSCRIPTION}"
echo "location: ${LOCATION}  vm_size: ${VM_SIZE}  nodes: ${NODE_COUNT}"

echo "== resource providers =="
for provider in Microsoft.ContainerService Microsoft.ContainerRegistry \
  Microsoft.Storage Microsoft.Insights; do
  state="$(az provider show --namespace "${provider}" --query registrationState -o tsv)"
  echo "${provider}: ${state}"
  [[ "${state}" == "Registered" ]] || {
    echo "FAIL: provider ${provider} is ${state}. Register with:" >&2
    echo "  az provider register --namespace ${provider}" >&2
    exit 1
  }
done

echo "== SKU availability (${VM_SIZE} in ${LOCATION}) =="
restrictions="$(az vm list-skus --location "${LOCATION}" \
  --resource-type virtualMachines --size "${VM_SIZE}" \
  --query "[].restrictions[].reason" -o tsv 2>/dev/null || true)"
if [[ -n "${restrictions}" ]]; then
  echo "FAIL: ${VM_SIZE} is blocked here (${restrictions})." >&2
  echo "Check another region if the verified size is unavailable; use AZURE_VM_SIZE=Standard_D4as_v4" >&2
  echo "(4 vCPU / 16 GB, non-burstable) or another allowed region." >&2
  echo "List options with:" >&2
  echo "  az vm list-skus --location ${LOCATION} --resource-type virtualMachines \\" >&2
  echo "    --query \"[?contains(name,'B2')].{sku:name,rest:restrictions}\" -o table" >&2
  exit 1
fi
echo "SKU clear."

echo "== vCPU quota =="
# Student subscriptions report classic names here (cores, standardBSFamily,
# standardBasv2Family) — NOT "Total Regional vCPUs". Deallocated VMs still
# consume quota, so list them when headroom is missing.
az vm list-usage --location "${LOCATION}" \
  --query "[?contains(name.value, 'cores') || contains(name.value, 'amily')].{family:name.value, used:currentValue, limit:limit}" \
  -o table
[[ "${VM_SIZE}" == Standard_D4as_v4 ]] || { echo "Use the verified 4-vCPU non-burstable system node" >&2; exit 1; }
FAMILY="standardDASv4Family"
needed=$((NODE_COUNT * 4))
usage="$(az vm list-usage --location "${LOCATION}" \
  --query "{cores: [?name.value=='cores'] | [0], family: [?name.value=='${FAMILY}'] | [0]}" \
  -o json)"
# NOTE: usage travels via env because the python block already takes its
# stdin from the heredoc below (a pipe into `python3 ... <<EOF` would lose).
USAGE_JSON="${usage}" python3 - "${needed}" "${NODE_COUNT}" <<'EOF'
import json, os, sys
needed, nodes = int(sys.argv[1]), sys.argv[2]
usage = json.loads(os.environ["USAGE_JSON"])
problems = []
for key in ("cores", "family"):
    entry = usage.get(key) or {}
    used, limit = int(entry.get("currentValue", 0)), int(entry.get("limit", 0))
    free = limit - used
    print(f"{key}: used {used}/{limit} (need {needed})")
    if free < needed:
        problems.append(key)
if problems:
    print("FAIL: insufficient quota for "
          f"{nodes} x 2-vCPU nodes (student quotas cannot be raised).", file=sys.stderr)
    print("Deallocated VMs still count — list them with:", file=sys.stderr)
    print("  az vm list -d -o table", file=sys.stderr)
    print("Delete leftovers (frees cores AND family quota), or switch SKU/region.", file=sys.stderr)
    sys.exit(1)
EOF
echo "quota clear."

echo "PREFLIGHT PASS: safe to run the human apply (CONFIRMATION=DEPLOY make azure ACTION=deploy)"
