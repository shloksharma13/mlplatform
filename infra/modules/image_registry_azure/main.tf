# Azure parity for kind-registry / ECR: Azure Container Registry (ACR) Basic SKU
# with mapped repositories for all service images.

data "azurerm_client_config" "current" {}

locals {
  # ACR name must be alphanumeric only, 5-50 characters.
  sanitized_name = substr(replace(lower("${var.project}reg${substr(replace(data.azurerm_client_config.current.subscription_id, "-", ""), 0, 8)}"), "/[^a-z0-9]/", ""), 0, 50)
}

resource "azurerm_container_registry" "this" {
  name                = local.sanitized_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}

variable "project" {
  description = "Project prefix sanitized into the ACR name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group holding the registry."
  type        = string
}

variable "tags" {
  description = "Tags applied to the registry."
  type        = map(string)
  default     = {}
}

output "login_server" {
  description = "ACR login server (image repository prefix)."
  value       = azurerm_container_registry.this.login_server
}

output "registry_id" {
  description = "Resource ID of the registry (scope for the AKS AcrPull assignment)."
  value       = azurerm_container_registry.this.id
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
