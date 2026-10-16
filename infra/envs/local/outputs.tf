output "kubeconfig" {
  description = "Path of the kubeconfig file written by the kind provider."
  value       = module.cluster.kubeconfig_path
}

output "registry_url" {
  description = "Host-facing URL of the local container registry."
  value       = module.registry.registry_url
}

output "s3_endpoint" {
  description = "Cluster-internal MinIO S3 endpoint."
  value       = module.platform_services.s3_endpoint
}

output "buckets" {
  description = "Object-store buckets created by the MinIO bucket Job."
  value       = module.platform_services.buckets
}

output "db_host" {
  description = "Cluster-internal PostgreSQL hostname."
  value       = module.platform_services.db_host
}

output "redis_host" {
  description = "Cluster-internal Redis hostname."
  value       = module.platform_services.redis_host
}
