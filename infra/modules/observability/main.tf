# Observability stack (P5): kube-prometheus-stack, Loki, Alloy,
# plus our dashboards/alerts content chart. Versions pinned; see docs/VERSIONS.md.

resource "kubernetes_namespace_v1" "observability" {
  metadata {
    name = var.namespace
  }
}

resource "random_password" "grafana" {
  length  = 24
  special = false
}

resource "kubernetes_secret_v1" "grafana_admin" {
  metadata {
    name      = "grafana-admin"
    namespace = kubernetes_namespace_v1.observability.metadata[0].name
  }

  data = {
    "admin-user"     = var.grafana_user
    "admin-password" = random_password.grafana.result
  }

  depends_on = [kubernetes_namespace_v1.observability]
}

# All three upstream charts are vendored under infra/vendor/ (see
# docs/DECISIONS.md): the asset host times out for helm/terraform downloads.
resource "helm_release" "kube_prometheus_stack" {
  name      = "kube-prometheus-stack"
  chart     = "${path.module}/../../../infra/vendor/kube-prometheus-stack-91.8.2.tgz"
  namespace = kubernetes_namespace_v1.observability.metadata[0].name

  values = [
    file("${path.module}/../../charts/observability/values-prometheus.yaml"),
  ]

  wait    = true
  timeout = 900

  depends_on = [kubernetes_secret_v1.grafana_admin]
}

resource "helm_release" "loki" {
  name  = "loki"
  chart = "${path.module}/../../../infra/vendor/loki-7.3.0.tgz"
  # Vendored (see docs/DECISIONS.md): the upstream asset host times out
  # for helm/terraform downloads, while the pinned .tgz is byte-identical.
  namespace = kubernetes_namespace_v1.observability.metadata[0].name

  values = [
    file("${path.module}/../../charts/observability/values-loki.yaml"),
  ]

  wait    = true
  timeout = 600

  depends_on = [helm_release.kube_prometheus_stack]
}

resource "helm_release" "alloy" {
  name      = "alloy"
  chart     = "${path.module}/../../../infra/vendor/alloy-1.13.0.tgz"
  namespace = kubernetes_namespace_v1.observability.metadata[0].name

  values = [
    file("${path.module}/../../charts/observability/values-alloy.yaml"),
  ]

  wait    = true
  timeout = 600

  depends_on = [helm_release.loki]
}

resource "helm_release" "observability_content" {
  name      = "observability-content"
  chart     = "${path.module}/../../charts/observability"
  namespace = kubernetes_namespace_v1.observability.metadata[0].name

  wait    = true
  timeout = 300

  depends_on = [helm_release.kube_prometheus_stack]
}

variable "namespace" {
  description = "Namespace for the observability releases."
  type        = string
  default     = "observability"
}

variable "grafana_user" {
  description = "Grafana admin username."
  type        = string
  default     = "admin"
}

variable "kube_prometheus_stack_version" {
  description = "Pinned kube-prometheus-stack chart version."
  type        = string
  default     = "91.8.2"
}

variable "loki_version" {
  description = "Pinned Loki chart version."
  type        = string
  default     = "7.3.0"
}

variable "alloy_version" {
  description = "Pinned Alloy chart version."
  type        = string
  default     = "1.13.0"
}

output "grafana_url" {
  description = "Host URL for Grafana (NodePort 30030 -> host 3000)."
  value       = "http://localhost:3000"
}

output "prometheus_url" {
  description = "Host URL for Prometheus (NodePort 30090 -> host 9090)."
  value       = "http://localhost:9090"
}

output "alertmanager_url" {
  description = "Host URL for Alertmanager (NodePort 30093 -> host 9093)."
  value       = "http://localhost:9093"
}
