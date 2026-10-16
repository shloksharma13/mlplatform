#!/usr/bin/env bash
# One Azure interface, including complete state-independent test teardown.
# Billable/destructive operations require an explicit human workflow confirmation.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/azure_oidc.sh
WORKFLOW_ACTION="${ACTION:-plan}"
ACTION="${1:-plan}"
TF=(terraform -chdir=infra/envs/azure)

if [[ "${ACTION}" == connect ]]; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  SUBSCRIPTION="$(az account show --query id -o tsv)"
  TENANT="$(az account show --query tenantId -o tsv)"
  BUDGET_EMAIL="$(az ad signed-in-user show --query mail -o tsv)"
  [[ "${BUDGET_EMAIL}" == *@* ]] || { echo "Set a real Azure budget email" >&2; exit 1; }
  PROJECT=mlp
  ENVIRONMENT=test
  RESOURCE_GROUP="${PROJECT}-${ENVIRONMENT}-rg"
  STATE_GROUP="${PROJECT}-${ENVIRONMENT}-state-rg"
  STATE_ACCOUNT="mlptf$(printf '%s' "${SUBSCRIPTION}" | tr -d '-' | cut -c1-16)"
  IDENTITY_GROUP=mlp-github-identity-rg
  IDENTITY_NAME=mlplatform-github-actions
  az provider register --namespace Microsoft.ManagedIdentity --wait
  if [[ "$(az group exists --name "${IDENTITY_GROUP}")" == true ]]; then
    owner="$(az group show --name "${IDENTITY_GROUP}" --query tags.Repository -o tsv)"
    [[ "${owner}" == "${REPO}" ]] || { echo "Identity group belongs to another project" >&2; exit 1; }
  else
    az group create --name "${IDENTITY_GROUP}" --location indiasouthcentral --tags "Repository=${REPO}" >/dev/null
  fi
  identities="$(az identity list --resource-group "${IDENTITY_GROUP}" --query "[?name=='${IDENTITY_NAME}'].name" -o tsv)"
  [[ -n "${identities}" ]] || az identity create --name "${IDENTITY_NAME}" --resource-group "${IDENTITY_GROUP}" --location indiasouthcentral --tags "Repository=${REPO}" >/dev/null
  APP_ID="$(az identity show --name "${IDENTITY_NAME}" --resource-group "${IDENTITY_GROUP}" --query clientId -o tsv)"
  PRINCIPAL_ID="$(az identity show --name "${IDENTITY_NAME}" --resource-group "${IDENTITY_GROUP}" --query principalId -o tsv)"
  federation="$(az identity federated-credential list --identity-name "${IDENTITY_NAME}" --resource-group "${IDENTITY_GROUP}" --query "[?name=='github-azure'].name" -o tsv)"
  [[ -n "${federation}" ]] || az identity federated-credential create --name github-azure --identity-name "${IDENTITY_NAME}" --resource-group "${IDENTITY_GROUP}" --issuer https://token.actions.githubusercontent.com --subject "repo:${REPO}:environment:azure" --audiences api://AzureADTokenExchange >/dev/null
  scope="/subscriptions/${SUBSCRIPTION}"
  for role in Contributor 'Role Based Access Control Administrator' 'Storage Blob Data Contributor' AcrPush; do
    existing="$(az role assignment list --scope "${scope}" --query "[?principalId=='${PRINCIPAL_ID}' && roleDefinitionName=='${role}'].id" -o tsv)"
    [[ -n "${existing}" ]] || az role assignment create --assignee-object-id "${PRINCIPAL_ID}" --assignee-principal-type ServicePrincipal --role "${role}" --scope "${scope}" >/dev/null
  done
  PUBLIC_IP="$(curl --fail --silent --show-error https://api.ipify.org)"
  # Azure observed a different egress address for browser traffic in the same
  # tester NAT pool, so allow its aligned /30 instead of the single lookup IP.
  PUBLIC_CIDR="$(python3 -c 'import ipaddress,sys; print(ipaddress.ip_network(sys.argv[1] + "/30", strict=False))' "${PUBLIC_IP}")"
  variables="$(mktemp)"
  cat > "${variables}" <<VARS
AZURE_CLIENT_ID=${APP_ID}
AZURE_TENANT_ID=${TENANT}
AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION}
AZURE_RESOURCE_GROUP=${RESOURCE_GROUP}
AZURE_NODE_RESOURCE_GROUP=${PROJECT}-${ENVIRONMENT}-nodes-rg
TF_STATE_RESOURCE_GROUP=${STATE_GROUP}
TF_STATE_ACCOUNT=${STATE_ACCOUNT}
AZURE_PROJECT=${PROJECT}
AZURE_ENVIRONMENT=${ENVIRONMENT}
AZURE_LOCATION=indiasouthcentral
AZURE_VM_SIZE=Standard_D4as_v4
AZURE_KUBERNETES_VERSION=1.36.4
AZURE_BUDGET_EMAIL=${BUDGET_EMAIL}
AZURE_ALLOWED_CIDR=${PUBLIC_CIDR}
VARS
  gh variable set --env azure --env-file "${variables}" --repo "${REPO}"
  rm -f "${variables}"
  echo "GitHub OIDC and Azure variables configured. No billable resources created."
  exit
fi

check() {
  if [[ "${ACTION}" == kill || "${ACTION}" == expired ]]; then
    [[ "${CONFIRMATION:-}" == KILL ]] || { echo "Human confirmation must be KILL" >&2; exit 1; }
    for key in ARM_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP TF_STATE_RESOURCE_GROUP; do
      [[ -n "${!key:-}" ]] || { echo "Missing GitHub Azure variable: ${key}" >&2; exit 1; }
    done
    [[ "${AZURE_RESOURCE_GROUP}" != "${TF_STATE_RESOURCE_GROUP}" ]] || { echo "App and state resource groups must be separate" >&2; exit 1; }
    return
  fi
  for key in ARM_CLIENT_ID ARM_TENANT_ID ARM_SUBSCRIPTION_ID TF_STATE_ACCOUNT TF_VAR_kubernetes_version TF_VAR_budget_email; do
    [[ -n "${!key:-}" ]] || { echo "Missing GitHub Azure variable: ${key}" >&2; exit 1; }
  done
  case "${ACTION:-plan}" in
    deploy|wake|destroy)
      [[ "${CONFIRMATION:-}" == "${ACTION^^}" ]] || { echo "Human confirmation must be ${ACTION^^}" >&2; exit 1; } ;;
    release)
      [[ "${VERSION:-}" =~ ^[0-9]+$ ]] || { echo "Release VERSION must be a numeric MLflow version" >&2; exit 1; } ;;
    dashboard)
      [[ -n "${AZURE_ALLOWED_CIDR:-}" ]] || { echo "Dashboard action requires AZURE_ALLOWED_CIDR" >&2; exit 1; } ;;
    check|plan|verify|sleep|init) ;;
    *) echo "Unknown Azure action: ${ACTION}" >&2; exit 2 ;;
  esac
}
# The check command validates the selected workflow ACTION before authenticating.
if [[ "${ACTION}" == check ]]; then ACTION="${WORKFLOW_ACTION}"; check; exit; fi
check
if [[ "${ACTION}" == kill || "${ACTION}" == expired ]]; then
  az account set --subscription "${ARM_SUBSCRIPTION_ID}"
fi
if [[ "${ACTION}" == expired ]]; then
  due=false
  existing=false
  for group in "${AZURE_RESOURCE_GROUP}" "${TF_STATE_RESOURCE_GROUP}"; do
    [[ "$(az group exists --name "${group}")" == true ]] || continue
    existing=true
    deadline="$(az group show --name "${group}" --query tags.ExpiresAt -o tsv)"
    [[ -n "${deadline}" ]] || { echo "Expiry missing on ${group}; use manual KILL" >&2; exit 1; }
    expired="$(python3 - "${deadline}" <<'EXPIRY_CHECK'
from datetime import datetime, timezone
import sys
print("true" if datetime.now(timezone.utc) >= datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")) else "false")
EXPIRY_CHECK
)"
    [[ "${expired}" != true ]] || due=true
  done
  if [[ "${existing}" == false && -n "${AZURE_NODE_RESOURCE_GROUP:-}" ]]; then
    if [[ "$(az group exists --name "${AZURE_NODE_RESOURCE_GROUP}")" == true ]]; then
      echo "Orphaned project node group remains after app/state removal."
      due=true
    fi
  fi
  if [[ "${due}" != true ]]; then
    echo "No expired Azure test session."
    if [[ "${existing}" == false && -n "${GH_TOKEN:-}" ]]; then
      gh api --method PUT "repos/${GITHUB_REPOSITORY}/actions/workflows/expiry.yml/disable"
    fi
    exit
  fi
  ACTION=kill
fi
# Full teardown does not need a readable backend, providers, or Kubernetes.
if [[ "${ACTION}" == kill ]]; then
  OWNER="${GITHUB_REPOSITORY:-JaiiMukundh/mlplatform}"
  GROUPS_TO_DELETE=("${AZURE_RESOURCE_GROUP}")
  if [[ "$(az group exists --name "${AZURE_RESOURCE_GROUP}")" == true ]]; then
    node_groups="$(az aks list --resource-group "${AZURE_RESOURCE_GROUP}" --query '[].nodeResourceGroup' -o tsv)"
    while IFS= read -r group; do
      [[ -z "${group}" ]] || GROUPS_TO_DELETE+=("${group}")
    done <<< "${node_groups}"
  fi
  [[ -z "${AZURE_NODE_RESOURCE_GROUP:-}" ]] || GROUPS_TO_DELETE+=("${AZURE_NODE_RESOURCE_GROUP}")
  GROUPS_TO_DELETE+=("${TF_STATE_RESOURCE_GROUP}")
  # Validate ownership for every group before deleting anything. AKS node groups
  # are owned through their managedBy link to the cluster in our app group.
  for group in "${GROUPS_TO_DELETE[@]}"; do
    [[ "$(az group exists --name "${group}")" == true ]] || continue
    ownership="$(az group show --name "${group}" --query '{repository:tags.Repository,managedBy:managedBy}' -o json)"
    OWNERSHIP="${ownership}" python3 - "${OWNER}" "${ARM_SUBSCRIPTION_ID}" "${AZURE_RESOURCE_GROUP}" <<'OWNERSHIP_CHECK'
import json, os, sys
owner, subscription, app_group = sys.argv[1:]
record = json.loads(os.environ["OWNERSHIP"])
cluster_prefix = f"/subscriptions/{subscription}/resourceGroups/{app_group}/providers/Microsoft.ContainerService/managedClusters/"
if record.get("repository") != owner and not (record.get("managedBy") or "").lower().startswith(cluster_prefix.lower()):
    sys.exit("Refusing kill: resource group is not owned by this repository")
OWNERSHIP_CHECK
  done
  budget_name="${TF_VAR_project:-mlp}-${TF_VAR_environment:-test}-monthly"
  budget_id="/subscriptions/${ARM_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.Consumption/budgets/${budget_name}"
  existing_budget="$(az consumption budget show --budget-name "${budget_name}" \
    --resource-group "${AZURE_RESOURCE_GROUP}" --query id -o tsv 2>/dev/null || true)"
  if [[ -n "${existing_budget}" ]]; then
    [[ "${existing_budget,,}" == "${budget_id,,}" ]] || {
      echo "Refusing kill: project budget ID does not match its owned resource group" >&2
      exit 1
    }
    az consumption budget delete --budget-name "${budget_name}" \
      --resource-group "${AZURE_RESOURCE_GROUP}" >/dev/null
    echo "Deleted project cost budget: ${budget_name}"
  fi
  for group in "${GROUPS_TO_DELETE[@]}"; do
    [[ "$(az group exists --name "${group}")" == true ]] || continue
    echo "Deleting project resource group: ${group}"
    az group delete --name "${group}" --yes
  done
  for group in "${GROUPS_TO_DELETE[@]}"; do
    [[ "$(az group exists --name "${group}")" == false ]] || { echo "Kill incomplete: ${group} still exists" >&2; exit 1; }
  done
  message="KILL VERIFIED: app, AKS node groups, and Terraform state group are absent. Test data and model history were deleted."
  echo "${message}"
  if [[ -n "${GH_TOKEN:-}" ]]; then
    gh api --method PUT "repos/${GITHUB_REPOSITORY}/actions/workflows/expiry.yml/disable"
  fi
  [[ -z "${GITHUB_STEP_SUMMARY:-}" ]] || echo "${message}" >> "${GITHUB_STEP_SUMMARY}"
  exit
fi
if [[ "${ACTION}" == deploy ]]; then
  : "${AZURE_ALLOWED_CIDR:?set the tester network CIDR in GitHub environment azure}"
  : "${TF_STATE_RESOURCE_GROUP:?set the project-owned backend resource group}"
  python3 - "${AZURE_ALLOWED_CIDR}" <<'CIDR_CHECK'
import ipaddress,sys
ipaddress.ip_network(sys.argv[1], strict=False)
CIDR_CHECK
  export TF_VAR_expires_at="$(date -u -d '+2 hours' +%FT%TZ)"
  gh api --method PUT "repos/${GITHUB_REPOSITORY}/actions/workflows/expiry.yml/enable"
  if [[ "$(az group exists --name "${TF_STATE_RESOURCE_GROUP}")" == true ]]; then
    owner="$(az group show --name "${TF_STATE_RESOURCE_GROUP}" --query tags.Repository -o tsv)"
    [[ "${owner}" == "${GITHUB_REPOSITORY}" ]] || { echo "Backend group belongs to another project" >&2; exit 1; }
    az group update --name "${TF_STATE_RESOURCE_GROUP}" --set "tags.ExpiresAt=${TF_VAR_expires_at}" >/dev/null
  else
    az group create --name "${TF_STATE_RESOURCE_GROUP}" --location "${TF_VAR_location}" \
      --tags "Repository=${GITHUB_REPOSITORY}" "ExpiresAt=${TF_VAR_expires_at}" >/dev/null
  fi
  touch /tmp/mlp-startup-started
  accounts="$(az storage account list --resource-group "${TF_STATE_RESOURCE_GROUP}" --query "[?name=='${TF_STATE_ACCOUNT}'].name" -o tsv)"
  if [[ -z "${accounts}" ]]; then
    az storage account create --name "${TF_STATE_ACCOUNT}" --resource-group "${TF_STATE_RESOURCE_GROUP}" \
      --location "${TF_VAR_location}" --sku Standard_LRS --kind StorageV2 \
      --https-only true --min-tls-version TLS1_2 --allow-blob-public-access false \
      --allow-shared-key-access false --tags "Repository=${GITHUB_REPOSITORY}" >/dev/null
  fi
  for attempt in $(seq 1 30); do
    if az storage container create --name tfstate --account-name "${TF_STATE_ACCOUNT}" --auth-mode login --public-access off >/dev/null; then
      break
    fi
    [[ "${attempt}" != 30 ]] || { echo "State-container creation failed" >&2; exit 1; }
    echo "Waiting for backend data-plane access (${attempt}/30)"; sleep 10
  done
fi
"${TF[@]}" init -input=false -lockfile=readonly \
  -backend-config="storage_account_name=${TF_STATE_ACCOUNT}" \
  -backend-config="container_name=tfstate" -backend-config="key=mlplatform.tfstate" \
  -backend-config="use_azuread_auth=true" -backend-config="use_oidc=true"
[[ "${ACTION}" == init ]] && exit 0
[[ "${ACTION}" == plan ]] && { "${TF[@]}" plan -input=false; exit; }
if [[ "${ACTION}" == deploy ]]; then
  # Resource-group deletion leaves Azure Cost Management budgets behind. Import
  # only the exact project-scoped alert so a clean backend can be recreated.
  budget_name="${TF_VAR_project}-${TF_VAR_environment}-monthly"
  expected_budget="/subscriptions/${ARM_SUBSCRIPTION_ID}/resourceGroups/${TF_VAR_project}-${TF_VAR_environment}-rg/providers/Microsoft.Consumption/budgets/${budget_name}"
  existing_budget="$(az consumption budget show --budget-name "${budget_name}" \
    --resource-group "${TF_VAR_project}-${TF_VAR_environment}-rg" --query id -o tsv 2>/dev/null || true)"
  if [[ -n "${existing_budget}" ]]; then
    [[ "${existing_budget,,}" == "${expected_budget,,}" ]] || {
      echo "Refusing deploy: existing budget ID does not match the project scope" >&2
      exit 1
    }
    if ! "${TF[@]}" state show azurerm_consumption_budget_resource_group.monthly >/dev/null 2>&1; then
      "${TF[@]}" import -input=false azurerm_consumption_budget_resource_group.monthly "${expected_budget}"
    fi
  fi
fi
if [[ "${ACTION}" == destroy ]]; then
  "${TF[@]}" plan -destroy -out=destroy.tfplan
  "${TF[@]}" apply -input=false destroy.tfplan
  exit
fi
if [[ "${ACTION}" == deploy ]]; then
  "${TF[@]}" plan -out=deploy.tfplan
  "${TF[@]}" apply -input=false deploy.tfplan
fi
RG="$("${TF[@]}" output -raw resource_group_name)"
CLUSTER="$("${TF[@]}" output -raw cluster_name)"
case "${ACTION}" in
  sleep) az aks stop --resource-group "${RG}" --name "${CLUSTER}"; exit ;;
  wake) az aks start --resource-group "${RG}" --name "${CLUSTER}"; exit ;;
esac
# AKS creation can outlast GitHub's five-minute OIDC assertion. Exchange a new
# job-scoped token before requesting Kubernetes credentials or pushing images.
refresh_azure_oidc_login
az aks get-credentials --resource-group "${RG}" --name "${CLUSTER}" --overwrite-existing
export PUBLIC_IP_NAME="$("${TF[@]}" output -raw public_ip_name)"
export PUBLIC_HOST="$("${TF[@]}" output -raw public_host)"
export AZURE_RESOURCE_GROUP="${RG}"

verify_dashboard() {
  kubectl -n observability rollout status deployment/dashboard --timeout=300s
  curl --insecure --fail --silent --show-error --connect-timeout 10 --max-time 15 \
    --retry 6 --retry-all-errors --retry-delay 2 \
    "https://${PUBLIC_HOST}/healthz"
  password="$(kubectl -n observability get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)"
  kubectl -n observability exec deploy/dashboard -- curl --fail --silent --insecure \
    --user "admin:${password}" https://localhost:8050/api/status > /tmp/mlp-dashboard-status.json
  python3 - <<'DASHBOARD_CHECK'
import json
status = json.load(open("/tmp/mlp-dashboard-status.json"))
assert status["nodes_ready"] >= 1 and status["serving_model"], status
assert all(status[key] for key in ["prometheus_online", "mlflow_online", "minio_online"]), status
print("Dashboard: cluster, serving, metrics, registry, and storage checks passed")
DASHBOARD_CHECK
  echo "Dashboard: https://${PUBLIC_HOST}"
}

verify() {
  kubectl -n serving rollout status deployment/model-api --timeout=180s
  local address=""
  for _ in $(seq 1 60); do
    address="$(kubectl -n serving get svc model-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
    [[ -n "${address}" ]] && break
    sleep 5
  done
  [[ -n "${address}" ]] || { echo 'Azure LoadBalancer has no address' >&2; exit 1; }
  curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 "http://${address}:8080/readyz"
  curl --fail --silent --show-error "http://${address}:8080/model-info"
  curl --fail --silent --show-error -H 'Content-Type: application/json' \
    -d '{"customer_ids":[7]}' "http://${address}:8080/predict"
  verify_dashboard
  echo "Model API: http://${address}:8080"
  [[ -z "${GITHUB_STEP_SUMMARY:-}" ]] || echo "Model API: http://${address}:8080" >> "${GITHUB_STEP_SUMMARY}"
}
[[ "${ACTION}" == verify ]] && { verify; exit; }
if [[ "${ACTION}" == dashboard ]]; then
  RUNNER_CIDR="$(curl --fail --silent --show-error https://api.ipify.org)/32"
  kubectl -n observability patch service dashboard --type=merge \
    -p="{\"spec\":{\"ports\":[{\"name\":\"https\",\"port\":443,\"protocol\":\"TCP\",\"targetPort\":\"https\"}],\"loadBalancerSourceRanges\":[\"${AZURE_ALLOWED_CIDR}\",\"${RUNNER_CIDR}\"]}}"
  verify_dashboard
  exit
fi
if [[ "${ACTION}" == release ]]; then
  export VERSION
  manifest="$(mktemp)"
  trap 'rm -f "${manifest}"' EXIT
  kubectl -n data get configmap retrain-job-template -o json | python3 -c '
import json, os, sys
pod = json.loads(json.load(sys.stdin)["data"]["pod-spec.json"])
c = pod["containers"][0]
c["env"].append({"name":"VERSION", "value": os.environ["VERSION"]})
c["args"][0] = c["args"][0].replace("python -m retrain.main", "bash scripts/model_release_local.sh \"$VERSION\" \"$IMAGE_TAG\"")
print(json.dumps({"apiVersion":"batch/v1", "kind":"Job", "metadata":{"name":"model-release", "namespace":"data"}, "spec":{"backoffLimit":0,"ttlSecondsAfterFinished":3600,"template":{"spec":pod}}}))
' > "${manifest}"
  bash scripts/run_job.sh "${manifest}" data
  exit
fi

export RUNNER_CIDR="$(curl --fail --silent --show-error https://api.ipify.org)/32"
ACR="$("${TF[@]}" output -raw acr_login_server)"
TAG="${TAG:-$(git rev-parse HEAD)}"
az acr login --name "${ACR%%.*}"
for service in training model-api mlflow remediator drift-monitor retrain dashboard; do
  docker build --file infra/Dockerfile --target "${service}" --tag "${ACR}/mlp-${service}:${TAG}" .
  docker push "${ACR}/mlp-${service}:${TAG}"
done
for namespace in data serving staging observability; do
  kubectl create namespace "${namespace}" --dry-run=client -o yaml | kubectl apply -f -
done
if ! kubectl -n data get secret postgres-auth >/dev/null 2>&1; then
  kubectl -n data create secret generic postgres-auth --from-literal=username=mlp --from-literal=postgres-password="$(openssl rand -hex 16)"
fi
if ! kubectl -n data get secret minio-auth >/dev/null 2>&1; then
  kubectl -n data create secret generic minio-auth --from-literal=root-user=minioadmin --from-literal=root-password="$(openssl rand -hex 16)"
fi
if ! kubectl -n observability get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n observability create secret generic grafana-admin --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -hex 16)"
fi
helm_apply() {
  local release="$1" namespace="$2" chart="$3"; shift 3
  helm upgrade --install "${release}" "${chart}" -n "${namespace}" --atomic --wait --timeout 15m "$@"
}
helm_apply kube-prometheus-stack observability infra/vendor/kube-prometheus-stack-91.8.2.tgz -f infra/charts/observability/values-prometheus.yaml
helm_apply loki observability infra/vendor/loki-7.3.0.tgz -f infra/charts/observability/values-loki.yaml
helm_apply alloy observability infra/vendor/alloy-1.13.0.tgz -f infra/charts/observability/values-alloy.yaml
helm_apply observability-content observability infra/charts/observability
for service in postgres minio; do
  helm_apply "${service}" data "infra/charts/${service}" --set auth.createSecret=false --set "auth.existingSecret=${service}-auth"
done
helm_apply redis data infra/charts/redis
helm_apply mlflow data infra/charts/mlflow \
  --set "image.repository=${ACR}/mlp-mlflow,image.tag=${TAG},resources.requests.memory=256Mi"

public_service() {
  local namespace="$1" name="$2" port="$3"
  kubectl -n "${namespace}" get service "${name}" -o json | \
    PUBLIC_PORT="${port}" python3 scripts/azure_public_service.py | kubectl replace -f -
}
public_service observability kube-prometheus-stack-grafana 3000
public_service observability kube-prometheus-stack-alertmanager 9093
public_service data mlflow 5000
public_service data minio-external 9000

# Port-forwards exist only on the ephemeral Actions runner, never on the laptop.
PF_PIDS=()
cleanup() { for pid in "${PF_PIDS[@]}"; do kill "${pid}" 2>/dev/null || true; done; }
trap cleanup EXIT
kubectl -n data port-forward svc/minio 9000:9000 >/tmp/mlp-minio-forward.log 2>&1 & PF_PIDS+=("$!")
kubectl -n data port-forward svc/mlflow 5000:5000 >/tmp/mlp-mlflow-forward.log 2>&1 & PF_PIDS+=("$!")
curl --fail --silent --retry 30 --retry-all-errors --retry-delay 2 http://localhost:9000/minio/health/ready
curl --fail --silent --retry 30 --retry-all-errors --retry-delay 2 http://localhost:5000/health
# Do not overwrite existing drifted features or train extra versions on reruns.
CHAMPION="$(curl --fail --silent 'http://localhost:5000/api/2.0/mlflow/registered-models/search' | python3 -c '
import json,sys
models=json.load(sys.stdin).get("registered_models",[])
print(next((a["version"] for m in models if m["name"]=="loan-default" for a in m.get("aliases",[]) if a["alias"]=="champion"),""))')"
export TRAINING_IMAGE="${ACR}/mlp-training:${TAG}"
if [[ -z "${CHAMPION}" ]]; then
  make seed-data publish-feast-data
  for job in apply-job.yaml materialize-job.yaml verify-job.yaml; do
    sed "s|localhost:5001/training:dev|${TRAINING_IMAGE}|g" "infra/jobs/feast-${job}" > /tmp/mlp-feast-job.yaml
    bash scripts/run_job.sh /tmp/mlp-feast-job.yaml data
  done
  bash scripts/train_mlflow.sh
  CHAMPION="$(curl --fail --silent 'http://localhost:5000/api/2.0/mlflow/registered-models/alias?name=loan-default&alias=champion' | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_version"]["version"])')"
fi
if ! curl --fail --silent 'http://localhost:5000/api/2.0/mlflow/registered-models/alias?name=loan-default&alias=baseline' >/dev/null; then
  curl --fail --silent --show-error -H 'Content-Type: application/json' \
    -d "{\"name\":\"loan-default\",\"alias\":\"baseline\",\"version\":\"${CHAMPION}\"}" \
    http://localhost:5000/api/2.0/mlflow/registered-models/alias
fi
SKIP_SMOKE=1 DEPLOY_TARGET=azure MODEL_API_REPOSITORY="${ACR}/mlp-model-api" \
  bash scripts/deploy.sh model-api serving "${TAG}" --set "model.version=${CHAMPION}"
helm_apply remediator observability infra/charts/remediator --set "image.repository=${ACR}/mlp-remediator,image.tag=${TAG}"
PG_USER="$(kubectl -n data get secret postgres-auth -o jsonpath='{.data.username}' | base64 -d)"
PG_PASS="$(kubectl -n data get secret postgres-auth -o jsonpath='{.data.postgres-password}' | base64 -d)"
kubectl -n observability create secret generic drift-monitor-db \
  --from-literal=database-url="postgresql+psycopg://${PG_USER}:${PG_PASS}@postgres.data.svc:5432/mlp" --dry-run=client -o yaml | kubectl apply -f -
helm_apply retrain data infra/charts/retrain --set "image.repository=${ACR}/mlp-retrain,image.tag=${TAG}" \
  --set "env.MODEL_API_REPOSITORY=${ACR}/mlp-model-api,env.TRAINING_IMAGE=${TRAINING_IMAGE},env.IMAGE_TAG=${TAG},env.DEPLOY_TARGET=azure,env.PUBLIC_IP_NAME=${PUBLIC_IP_NAME},env.AZURE_RESOURCE_GROUP=${RG},env.AZURE_ALLOWED_CIDR=${AZURE_ALLOWED_CIDR}"
helm_apply drift-monitor observability infra/charts/drift-monitor --set "image.repository=${ACR}/mlp-drift-monitor,image.tag=${TAG}"
MINIO_USER="$(kubectl -n data get secret minio-auth -o jsonpath='{.data.root-user}' | base64 -d)"
MINIO_PASS="$(kubectl -n data get secret minio-auth -o jsonpath='{.data.root-password}' | base64 -d)"
kubectl -n observability create secret generic dashboard-data \
  --from-literal=access-key="${MINIO_USER}" --from-literal=secret-key="${MINIO_PASS}" --dry-run=client -o yaml | kubectl apply -f -
if ! kubectl -n observability get secret dashboard-tls >/dev/null 2>&1; then
  tls_dir="$(mktemp -d)"
  openssl req -x509 -nodes -newkey rsa:2048 -days 2 \
    -subj "/CN=${PUBLIC_HOST}" -addext "subjectAltName=DNS:${PUBLIC_HOST}" \
    -keyout "${tls_dir}/tls.key" -out "${tls_dir}/tls.crt" >/dev/null 2>&1
  kubectl -n observability create secret tls dashboard-tls --cert="${tls_dir}/tls.crt" --key="${tls_dir}/tls.key"
  rm -rf "${tls_dir}"
fi
export DASHBOARD_IMAGE="${ACR}/mlp-dashboard:${TAG}" CLUSTER_NAME="${CLUSTER}" IMAGE_TAG="${TAG}" MODEL_API_REPOSITORY="${ACR}/mlp-model-api"
python3 - <<'DASHBOARD_RENDER' | kubectl apply -f -
import os,re
from pathlib import Path
manifest=Path("infra/dashboard.yaml").read_text()
values={**os.environ,"RESOURCE_GROUP":os.environ["AZURE_RESOURCE_GROUP"],"ALLOWED_CIDR":os.environ["AZURE_ALLOWED_CIDR"]}
print(re.sub(r"__([A-Z_]+)__",lambda match:values[match[1]],manifest))
DASHBOARD_RENDER

verify
SESSION_EXPIRES_AT="$(date -u -d '+2 hours' +%FT%TZ)"
for group in "${RG}" "${TF_STATE_RESOURCE_GROUP}"; do
  az group update --name "${group}" --set "tags.ExpiresAt=${SESSION_EXPIRES_AT}" >/dev/null
done
echo "Automatic full teardown due at ${SESSION_EXPIRES_AT} UTC; scheduled checks every 15 minutes."
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "Dashboard: https://${PUBLIC_HOST} (admin login; self-signed test certificate)"
    echo "Test session expires: ${SESSION_EXPIRES_AT}; cleanup scheduling may be delayed."
    echo "End testing immediately: run the azure workflow, action kill, confirmation KILL."
    echo "Kill deletes the app, node resources, images, disks, model/data history, and backend."
  } >> "${GITHUB_STEP_SUMMARY}"
fi
