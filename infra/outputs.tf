output "environment" {
  value = terraform.workspace
}

output "state_key" {
  description = "Dónde quedó guardado el estado de este workspace en S3."
  value       = "s3://aegisvolt-tfstate/env:/${terraform.workspace}/aegisvolt/terraform.tfstate"
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "public_subnet_ids" {
  value = module.network.public_subnet_ids
}

output "vpc_endpoints" {
  value = merge(module.network.gateway_endpoint_ids, module.network.interface_endpoint_ids)
}

output "dynamodb_tables" {
  value = module.dynamodb.table_names
}

output "log_groups" {
  value = module.observability.log_group_names
}

output "ecr_repositories" {
  value = module.ecr.repository_urls
}

output "eks_cluster_name" {
  value = var.enable_eks ? module.eks[0].cluster_name : null
}

output "eks_cluster_endpoint" {
  value = var.enable_eks ? module.eks[0].cluster_endpoint : null
}

output "k8s_private_link" {
  description = "VPC Endpoint Service (PrivateLink) hacia el gateway del clúster y el endpoint que lo consume."
  value       = var.enable_eks ? module.k8s_ingress[0].private_link : null
}

output "api_gateway" {
  value = module.api_gateway.summary
}
