#!/usr/bin/env bash
# Refresh Azure CLI's federated session when long-running provisioning expires it.
refresh_azure_oidc_login() {
  [[ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]] || return 0
  : "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?GitHub OIDC request token is missing}"
  : "${ARM_CLIENT_ID:?Azure client ID is missing}"
  : "${ARM_TENANT_ID:?Azure tenant ID is missing}"
  : "${ARM_SUBSCRIPTION_ID:?Azure subscription ID is missing}"

  local federated_token
  federated_token="$(curl --fail --silent --show-error --get \
    --data-urlencode 'audience=api://AzureADTokenExchange' \
    --header "Authorization: Bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
    "${ACTIONS_ID_TOKEN_REQUEST_URL}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])')"
  [[ -n "${federated_token}" ]] || { echo "GitHub OIDC token response was empty" >&2; return 1; }
  az login --service-principal --username "${ARM_CLIENT_ID}" \
    --tenant "${ARM_TENANT_ID}" --federated-token "${federated_token}" \
    --output none
  az account set --subscription "${ARM_SUBSCRIPTION_ID}"
  unset federated_token
}
