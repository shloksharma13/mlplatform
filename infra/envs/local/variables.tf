variable "cluster_name" {
  description = "Name of the kind cluster."
  type        = string
  default     = "mlp"
}

variable "kubeconfig_path" {
  description = "Where the kind provider writes the kubeconfig file."
  type        = string
  default     = ""
}

variable "registry_port" {
  description = "Host port publishing the local container registry."
  type        = number
  default     = 5001
}
