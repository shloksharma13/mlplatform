variable "subscription_id" {
  description = "Azure Subscription ID. No default: every student has their own subscription, so this must come from terraform.tfvars (see terraform.tfvars.example)."
  type        = string
}

variable "project" {
  description = "Project prefix for resource names."
  type        = string
  default     = "mlp"
}

variable "environment" {
  description = "Environment suffix for resource names."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "indiasouthcentral"
}

variable "vnet_cidr" {
  description = "Address space for the VNet (used by the AKS subnet)."
  type        = string
  default     = "10.0.0.0/16"
}

variable "kubernetes_version" {
  description = "Exact supported AKS patch version, selected from the target region before deployment."
  type        = string
  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.kubernetes_version))
    error_message = "Set an exact AKS patch version supported in your region; no obsolete or floating default is selected."
  }
}

variable "node_count" {
  description = "Single 4-vCPU/16-GiB system node for the temporary test session."
  type        = number
  default     = 1
  validation {
    condition     = var.node_count == 1
    error_message = "This student test environment uses one node within its 4-vCPU family quota."
  }
}

variable "vm_size" {
  description = "Available non-burstable AKS system-node SKU verified against regional quota."
  type        = string
  default     = "Standard_D4as_v4"
  validation {
    condition     = var.vm_size == "Standard_D4as_v4"
    error_message = "Use the verified Standard_D4as_v4 size; B-series system nodes are unsupported."
  }
}

variable "budget_limit_usd" {
  description = "Monthly budget guardrail in USD."
  type        = number
  default     = 50
}

variable "budget_email" {
  description = "Email for budget alerts."
  type        = string
  default     = "your-email@example.com"
}

variable "budget_start_date" {
  description = "Budget window start (first of month, UTC)."
  type        = string
  default     = "2026-10-01T00:00:00Z"
}

variable "expires_at" {
  description = "UTC test-session deadline; GitHub expiry deletes the full environment after this time."
  type        = string
  default     = ""
}
