# Local platform: registry -> kind cluster -> data services.
# The model-api release itself stays in scripts/deploy.sh (same as CI uses).
module "registry" {
  source        = "../../modules/registry"
  registry_port = var.registry_port
}

module "cluster" {
  source          = "../../modules/cluster_kind"
  cluster_name    = var.cluster_name
  kubeconfig_path = var.kubeconfig_path != "" ? var.kubeconfig_path : "${path.root}/kubeconfig"
  depends_on      = [module.registry]
}

module "platform_services" {
  source     = "../../modules/platform_services"
  depends_on = [module.cluster]
}

module "observability" {
  source     = "../../modules/observability"
  depends_on = [module.platform_services]
}

provider "kubernetes" {
  config_path = module.cluster.kubeconfig_path
}

provider "helm" {
  kubernetes = {
    config_path = module.cluster.kubeconfig_path
  }
}
