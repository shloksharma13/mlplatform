# Data layer + metrics-server, installed from our own pinned charts.
# Credentials are generated here (random_password) and consumed by the charts
# via existingSecret, so no password is ever committed to git.

resource "kubernetes_namespace_v1" "data" {
  metadata {
    name = var.namespace
  }
}

# Alphanumeric only: these passwords end up inside URLs (DATABASE_URL, S3
# endpoint overrides), where special characters would need escaping.
resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "random_password" "minio" {
  length  = 24
  special = false
}

resource "kubernetes_secret_v1" "postgres_auth" {
  metadata {
    name      = "postgres-auth"
    namespace = kubernetes_namespace_v1.data.metadata[0].name
  }

  data = {
    "username"          = var.postgres_user
    "postgres-password" = random_password.postgres.result
  }

  depends_on = [kubernetes_namespace_v1.data]
}

resource "kubernetes_secret_v1" "minio_auth" {
  metadata {
    name      = "minio-auth"
    namespace = kubernetes_namespace_v1.data.metadata[0].name
  }

  data = {
    "root-user"     = var.minio_user
    "root-password" = random_password.minio.result
  }

  depends_on = [kubernetes_namespace_v1.data]
}

resource "helm_release" "metrics_server" {
  name  = "metrics-server"
  chart = "${path.module}/../../../infra/vendor/metrics-server-3.14.0.tgz"
  # Vendored (see docs/DECISIONS.md): the asset host times out for helm/terraform.
  namespace = "kube-system"

  # Required on kind (same flag as the P2.3 imperative install).
  set = [{
    name  = "args[0]"
    value = "--kubelet-insecure-tls"
  }]

  wait    = true
  timeout = 300
}

resource "helm_release" "postgres" {
  name      = "postgres"
  chart     = "${path.module}/../../charts/postgres"
  namespace = kubernetes_namespace_v1.data.metadata[0].name

  set = [
    {
      name  = "auth.createSecret"
      value = "false"
    },
    {
      name  = "auth.existingSecret"
      value = kubernetes_secret_v1.postgres_auth.metadata[0].name
    },
  ]

  wait    = true
  timeout = 600

  depends_on = [kubernetes_secret_v1.postgres_auth]
}

resource "helm_release" "minio" {
  name      = "minio"
  chart     = "${path.module}/../../charts/minio"
  namespace = kubernetes_namespace_v1.data.metadata[0].name

  set = [
    {
      name  = "auth.createSecret"
      value = "false"
    },
    {
      name  = "auth.existingSecret"
      value = kubernetes_secret_v1.minio_auth.metadata[0].name
    },
  ]

  wait    = true
  timeout = 600

  depends_on = [kubernetes_secret_v1.minio_auth]
}

resource "helm_release" "redis" {
  name      = "redis"
  chart     = "${path.module}/../../charts/redis"
  namespace = kubernetes_namespace_v1.data.metadata[0].name

  wait    = true
  timeout = 600

  depends_on = [kubernetes_namespace_v1.data]
}

resource "helm_release" "mlflow" {
  name      = "mlflow"
  chart     = "${path.module}/../../charts/mlflow"
  namespace = kubernetes_namespace_v1.data.metadata[0].name

  wait    = true
  timeout = 600

  depends_on = [kubernetes_secret_v1.minio_auth, kubernetes_secret_v1.postgres_auth]
}

variable "namespace" {
  description = "Namespace for the data-layer releases (postgres, minio, redis)."
  type        = string
  default     = "data"
}

variable "metrics_server_version" {
  description = "Pinned metrics-server chart version."
  type        = string
  default     = "3.14.0"
}

variable "metrics_server_repository" {
  description = "Helm repository serving the metrics-server chart."
  type        = string
  default     = "https://kubernetes-sigs.github.io/metrics-server/"
}

variable "postgres_user" {
  description = "Postgres application username (stored in the managed Secret)."
  type        = string
  default     = "mlp"
}

variable "minio_user" {
  description = "MinIO root username (stored in the managed Secret)."
  type        = string
  default     = "minioadmin"
}

variable "buckets" {
  description = "Object-store buckets created by the MinIO bucket Job."
  type        = list(string)
  default     = ["mlflow-artifacts", "feast-offline", "ml-data"]
}

output "namespace" {
  description = "Namespace hosting the data-layer releases."
  value       = kubernetes_namespace_v1.data.metadata[0].name
}

output "s3_endpoint" {
  description = "Cluster-internal MinIO S3 endpoint."
  value       = "http://${helm_release.minio.name}.${var.namespace}.svc:9000"
}

output "buckets" {
  description = "Object-store buckets created by the MinIO bucket Job."
  value       = var.buckets
}

output "db_host" {
  description = "Cluster-internal PostgreSQL hostname."
  value       = "${helm_release.postgres.name}.${var.namespace}.svc"
}

output "redis_host" {
  description = "Cluster-internal Redis hostname."
  value       = "${helm_release.redis.name}.${var.namespace}.svc"
}

output "postgres_secret_name" {
  description = "Name of the Secret holding the Postgres credentials."
  value       = kubernetes_secret_v1.postgres_auth.metadata[0].name
}

output "minio_secret_name" {
  description = "Name of the Secret holding the MinIO credentials."
  value       = kubernetes_secret_v1.minio_auth.metadata[0].name
}

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
  }
}
