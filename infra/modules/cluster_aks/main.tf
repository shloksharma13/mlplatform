# Azure parity for "cluster": Azure Kubernetes Service (AKS) with
# OIDC issuer and a supported, quota-verified system-node size.

resource "azurerm_kubernetes_cluster" "this" {
  name                    = var.cluster_name
  location                = var.location
  resource_group_name     = var.resource_group_name
  dns_prefix              = var.cluster_name
  node_resource_group     = "${var.cluster_name}-nodes-rg"
  sku_tier                = "Free"
  node_os_upgrade_channel = "None"
  kubernetes_version      = var.kubernetes_version

  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  node_provisioning_profile {
    mode = "Manual"
  }

  default_node_pool {
    name            = "default"
    node_count      = var.node_count
    vm_size         = var.vm_size
    vnet_subnet_id  = var.vnet_subnet_id
    os_disk_size_gb = 30
    max_pods        = 50
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.cluster.id]
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    pod_cidr            = "10.244.0.0/16"
    load_balancer_sku   = "standard"
    service_cidr        = "10.100.0.0/16"
    dns_service_ip      = "10.100.0.10"
  }

  tags       = var.tags
  depends_on = [azurerm_role_assignment.network]
}

# Keep a small burst pool for platform services. The quota-checked system pool
# consumes the subscription's four DASv4 cores; one B-series node is available
# for the data and observability workloads in the test subscription.
resource "azurerm_kubernetes_cluster_node_pool" "auxiliary" {
  name                  = "auxiliary"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = "Standard_B2s"
  node_count            = 1
  mode                  = "User"
  vnet_subnet_id        = var.vnet_subnet_id
  os_disk_size_gb       = 30
  max_pods              = 30
  tags                  = var.tags
}

data "azurerm_client_config" "current" {}

resource "azurerm_user_assigned_identity" "cluster" {
  name                = "${var.cluster_name}-control-plane"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_role_assignment" "network" {
  principal_id                     = azurerm_user_assigned_identity.cluster.principal_id
  role_definition_name             = "Network Contributor"
  scope                            = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${var.resource_group_name}"
  skip_service_principal_aad_check = true
}

# Attach AKS Kubelet identity to ACR for pulling images without imagePullSecrets.
resource "azurerm_role_assignment" "acr_pull" {
  # The environment always provisions ACR. Keep count known before its ID exists.
  count = 1

  principal_id                     = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
  role_definition_name             = "AcrPull"
  scope                            = var.acr_id
  skip_service_principal_aad_check = true
}

variable "cluster_name" {
  description = "AKS cluster name (also used as DNS prefix)."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group holding the cluster."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version for the control plane and nodes."
  type        = string
  default     = "1.36.4"
}

variable "node_count" {
  description = "Node count in the default (system + workload) pool."
  type        = number
  default     = 1
}

variable "vm_size" {
  description = "VM SKU for the default node pool."
  type        = string
  default     = "Standard_D4as_v4"
}

variable "vnet_subnet_id" {
  description = "Subnet ID for the AKS node pool (VNet-integrated)."
  type        = string
}

variable "acr_id" {
  description = "ACR resource ID for the AcrPull role assignment (empty skips it)."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to the cluster."
  type        = map(string)
  default     = {}
}

output "cluster_name" {
  description = "AKS cluster name (for az aks get-credentials)."
  value       = azurerm_kubernetes_cluster.this.name
}

output "oidc_issuer_url" {
  description = "OIDC issuer URL for Workload Identity federated credentials."
  value       = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "kubelet_object_id" {
  description = "Object ID of the kubelet managed identity."
  value       = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
}

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "5.9.0"
    }
  }
}
