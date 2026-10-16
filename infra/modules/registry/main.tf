# The registry is intentionally imperative: kind's documented local-registry setup
# is a stateful Docker container plus a network attach, which the script performs
# idempotently. Terraform only tracks the script hash so edits force a re-run.
resource "terraform_data" "registry" {
  triggers_replace = {
    script_sha256 = filesha256("${path.module}/${var.script_path}")
  }

  provisioner "local-exec" {
    command = "bash \"${path.module}/${var.script_path}\""
    environment = {
      REGISTRY_NAME  = var.registry_name
      REGISTRY_PORT  = tostring(var.registry_port)
      REGISTRY_IMAGE = var.registry_image
    }
  }
}

variable "registry_name" {
  description = "Name of the local registry container."
  type        = string
  default     = "kind-registry"
}

variable "registry_port" {
  description = "Host port publishing the registry."
  type        = number
  default     = 5001
}

variable "registry_image" {
  description = "Pinned registry image (must include the digest)."
  type        = string
  default     = "registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
}

variable "script_path" {
  description = "Path to the idempotent registry bootstrap script, relative to this module."
  type        = string
  default     = "files/registry.sh"
}

output "registry_url" {
  description = "Host-facing URL of the local container registry."
  value       = "localhost:${var.registry_port}"
}

output "registry_name" {
  description = "Name of the local registry container."
  value       = var.registry_name
}

terraform {
  required_version = ">= 1.9.0"
  # terraform_data lives in the built-in provider: no version pin allowed.
}
