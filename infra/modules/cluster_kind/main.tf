# The kind cluster is managed imperatively through the kind CLI: the
# tehcyx/kind provider (v0.11.0, latest) bundles kind 0.31.0, whose kubeadm
# cannot init the pinned Kubernetes v1.37.0 nodes, while the kind v0.33.0 CLI
# creates the identical config cleanly. Same pattern as the registry module:
# Terraform tracks the rendered config hash, the script does the mutation.
resource "terraform_data" "cluster" {
  triggers_replace = {
    cluster_name    = var.cluster_name
    config_sha256   = local_file.kind_config.content_sha256
    create_sha256   = filesha256("${path.module}/files/create.sh")
    kind_config     = local.kind_config_file
    kubeconfig_file = local.kubeconfig_file
  }

  provisioner "local-exec" {
    command = "bash \"${path.module}/files/create.sh\""
    environment = {
      CLUSTER_NAME    = var.cluster_name
      KIND_CONFIG     = local.kind_config_file
      KUBECONFIG_FILE = local.kubeconfig_file
      REGISTRY_SCRIPT = "${path.module}/${var.registry_script_path}"
    }
  }

  provisioner "local-exec" {
    when    = destroy
    command = "bash \"${path.module}/files/delete.sh\""
    environment = {
      CLUSTER_NAME    = self.triggers_replace.cluster_name
      KIND_CONFIG     = self.triggers_replace.kind_config
      KUBECONFIG_FILE = self.triggers_replace.kubeconfig_file
    }
  }

  depends_on = [local_file.kind_config]
}

resource "local_file" "kind_config" {
  content = templatefile("${path.module}/kind-config.yaml.tftpl", {
    node_image              = var.node_image
    registry_host           = var.registry_host
    registry_container_host = var.registry_container_host
    port_mappings           = var.port_mappings
  })
  filename = local.kind_config_file
}

locals {
  kubeconfig_file  = var.kubeconfig_path != "" ? var.kubeconfig_path : "${path.root}/kubeconfig"
  kind_config_file = "${path.root}/.kind-config.${var.cluster_name}.yaml"
}

variable "cluster_name" {
  description = "Name of the kind cluster."
  type        = string
  default     = "mlp"
}

variable "node_image" {
  description = "Pinned kind node image (must include the digest)."
  type        = string
  default     = "kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
}

variable "kubeconfig_path" {
  description = "Where the kind provider writes the kubeconfig file."
  type        = string
  default     = ""
}

variable "registry_host" {
  description = "Registry hostname as seen from the host (kind-config mirror target)."
  type        = string
  default     = "localhost:5001"
}

variable "registry_container_host" {
  description = "Registry hostname as seen from inside kind nodes."
  type        = string
  default     = "kind-registry:5000"
}

variable "port_mappings" {
  description = "Host ports for service NodePorts (hostPort -> containerPort)."
  type        = map(number)
  default = {
    "8080" = 30080
    "8081" = 30081
    "3000" = 30030
    "5000" = 30500
    "9000" = 30900
    "9001" = 30901
    "9090" = 30090
    "9093" = 30093
  }
}

variable "registry_script_path" {
  description = "Registry bootstrap script, relative to this module (re-run after the cluster exists)."
  type        = string
  default     = "../registry/files/registry.sh"
}

output "cluster_name" {
  description = "Name of the kind cluster."
  value       = terraform_data.cluster.triggers_replace.cluster_name
}

output "kubeconfig_path" {
  description = "Path of the standalone kubeconfig file written at creation."
  # Guarded by an apply-time-known attribute so that provider configurations
  # built on this output (kubernetes/helm in envs/local) are deferred until
  # the cluster exists. Without this, those providers stat the kubeconfig
  # during plan and fail because the file is not written yet.
  value = terraform_data.cluster.id == null ? null : local.kubeconfig_file
}

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "2.9.1"
    }
  }
}
