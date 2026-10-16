# Azure native environment (AKS straight port of the old AWS design).
# Never apply without the human step in P9.6.
# Only init -backend=false, validate, and plan are allowed here. Applying is [HUMAN].
locals {
  name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Repository  = "JaiiMukundh/mlplatform"
    ExpiresAt   = var.expires_at
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "this" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = local.tags
}

module "network" {
  source = "../../modules/network_azure"

  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  vnet_cidr           = var.vnet_cidr
  tags                = local.tags
}

module "registry" {
  source = "../../modules/image_registry_azure"

  project             = var.project
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

module "cluster" {
  source = "../../modules/cluster_aks"

  cluster_name        = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  kubernetes_version  = var.kubernetes_version
  node_count          = var.node_count
  vm_size             = var.vm_size
  vnet_subnet_id      = module.network.aks_subnet_id
  acr_id              = module.registry.registry_id
  tags                = local.tags
}

# Cost guardrail for the demo window (P9.6 tears everything down).
resource "azurerm_monitor_action_group" "budget" {
  name                = "${local.name}-budget-ag"
  resource_group_name = azurerm_resource_group.this.name
  short_name          = "mlpbudget"

  email_receiver {
    name          = "owner"
    email_address = var.budget_email
  }

  tags = local.tags
}

resource "azurerm_consumption_budget_resource_group" "monthly" {
  name              = "${local.name}-monthly"
  resource_group_id = azurerm_resource_group.this.id

  amount     = var.budget_limit_usd
  time_grain = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Forecasted"

    contact_emails = [var.budget_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Actual"

    contact_emails = [var.budget_email]
  }
}

resource "azurerm_public_ip" "ingress" {
  name                = "${local.name}-ingress"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  allocation_method   = "Static"
  sku                 = "Standard"
  domain_name_label   = "${local.name}-${substr(replace(var.subscription_id, "-", ""), 0, 8)}"
  tags                = local.tags
}
