# -----------------------------------------------------------------------------
# AegisVolt · raíz de infraestructura (un solo código, tres workspaces).
#
#   terraform workspace select dev   && terraform apply -var-file=env/dev.tfvars
#   terraform workspace select test  && terraform apply -var-file=env/test.tfvars
#   terraform workspace select prod  && terraform apply -var-file=env/prod.tfvars
#
# Aislamiento entre ambientes (por qué un apply en test NO toca prod):
#   1. Estado separado: cada workspace tiene su propia key en S3 (env:/<ws>/...).
#      Terraform en el workspace test solo conoce los recursos de test.
#   2. Nombres separados: todo recurso lleva ${terraform.workspace} en el nombre
#      (aegisvolt-billing-test vs aegisvolt-billing-prod): no hay colisiones.
#   3. Guardas: el plan falla si el workspace no es dev/test/prod o si el
#      .tfvars no corresponde al workspace (evita aplicar prod.tfvars en test).
#   4. Prod tiene deletion_protection en DynamoDB.
# -----------------------------------------------------------------------------

locals {
  env = terraform.workspace
  # Con Floci (aws_endpoint definido) se omiten las 2 APIs que el emulador no implementa.
  on_emulator = var.aws_endpoint != null
  prefix      = "${var.project}-${local.env}"

  services = ["graphql-gateway", "station-reservation-service", "billing-wallet-service", "energy-dispatch-service"]
}

resource "terraform_data" "workspace_guard" {
  input = local.env

  lifecycle {
    precondition {
      condition     = contains(["dev", "test", "prod"], terraform.workspace)
      error_message = "Workspace inválido '${terraform.workspace}'. Use: terraform workspace select dev|test|prod (nunca 'default')."
    }
    precondition {
      condition     = var.environment == terraform.workspace
      error_message = "El archivo de variables es de '${var.environment}' pero el workspace es '${terraform.workspace}'. Use -var-file=env/${terraform.workspace}.tfvars."
    }
  }
}

module "network" {
  source = "./modules/network"

  prefix              = local.prefix
  environment         = local.env
  region              = var.region
  vpc_cidr            = var.vpc_cidr
  availability_zones  = var.availability_zones
  interface_endpoints = var.interface_endpoints

  depends_on = [terraform_data.workspace_guard]
}

module "dynamodb" {
  source = "./modules/dynamodb"

  project       = var.project
  environment   = local.env
  feature_flags = var.feature_flags

  depends_on = [terraform_data.workspace_guard]
}

module "observability" {
  source = "./modules/observability"

  project        = var.project
  environment    = local.env
  services       = local.services
  retention_days = var.log_retention_days

  depends_on = [terraform_data.workspace_guard]
}

module "ecr" {
  source = "./modules/ecr"

  prefix   = local.prefix
  services = local.services

  depends_on = [terraform_data.workspace_guard]
}

module "eks" {
  source = "./modules/eks"
  count  = var.enable_eks ? 1 : 0

  prefix              = local.prefix
  cluster_name        = "${local.prefix}-eks"
  kubernetes_version  = var.eks_version
  vpc_id              = module.network.vpc_id
  vpc_cidr            = var.vpc_cidr
  private_subnet_ids  = module.network.private_subnet_ids
  public_endpoint     = var.eks_public_endpoint
  node_instance_type  = var.node_instance_type
  node_desired_size   = var.node_desired_size
  dynamodb_table_arns = module.dynamodb.table_arns
  log_group_arns      = module.observability.log_group_arns
  gateway_node_port   = var.gateway_node_port
}

# NLB interno delante de Kubernetes + VPC Endpoint Service (PrivateLink) hacia
# los servicios del clúster. Lo consume el VPC Link de API Gateway.
module "k8s_ingress" {
  source = "./modules/k8s_ingress"
  count  = var.enable_eks ? 1 : 0

  prefix             = local.prefix
  vpc_id             = module.network.vpc_id
  private_subnet_ids = module.network.private_subnet_ids
  node_port          = var.gateway_node_port
  node_asg_names     = module.eks[0].node_autoscaling_group_names
  endpoint_sg_id     = module.network.endpoint_security_group_id

  # APIs que Floci no implementa: se crean solo contra AWS real.
  enable_private_link_service = !local.on_emulator
  attach_node_asg             = !local.on_emulator
}

module "api_gateway" {
  source = "./modules/api_gateway"

  prefix              = local.prefix
  environment         = local.env
  execute_api_vpce_id = module.network.interface_endpoint_ids["execute-api"]
  integration_mode    = var.enable_eks ? var.api_integration_mode : "http_proxy"
  nlb_arn             = var.enable_eks ? module.k8s_ingress[0].nlb_arn : null
  nlb_dns_name        = var.enable_eks ? module.k8s_ingress[0].nlb_dns_name : null
  local_backend_url   = var.local_backend_url
}
