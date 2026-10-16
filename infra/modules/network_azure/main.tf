# AKS network; AKS manages its own node NSG and Service source-range rules.

resource "azurerm_virtual_network" "this" {
  name                = "${var.name}-vnet"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_cidr]
  tags                = var.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "aks-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 8, 1)]
}

variable "name" {
  description = "Base name for the VNet and AKS subnet."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group holding the network."
  type        = string
}

variable "vnet_cidr" {
  description = "Address space for the VNet (a /16 provides the AKS subnet)."
  type        = string
  default     = "10.0.0.0/16"
}

variable "tags" {
  description = "Tags applied to taggable resources."
  type        = map(string)
  default     = {}
}

output "vnet_id" {
  description = "ID of the Virtual Network."
  value       = azurerm_virtual_network.this.id
}

output "aks_subnet_id" {
  description = "ID of the AKS node-pool subnet."
  value       = azurerm_subnet.aks.id
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
