variable "environment" {
  description = "Debe coincidir con el workspace seleccionado (dev | test | prod). Sirve de doble verificación."
  type        = string
  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "environment debe ser dev, test o prod."
  }
}

variable "project" {
  type    = string
  default = "aegisvolt"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "aws_endpoint" {
  description = "Endpoint de Floci (http://localhost:4566 en tu PC, http://host.docker.internal:4566 en el runner). null = AWS real."
  type        = string
  default     = "http://localhost:4566"
}

# ------------------------------------------------------------------- red
variable "vpc_cidr" {
  description = "CIDR de la VPC del ambiente. Distinto por ambiente para poder hacer peering sin solaparse."
  type        = string
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "interface_endpoints" {
  description = "Servicios con Interface VPC Endpoint (PrivateLink) en las subredes privadas."
  type        = list(string)
  default     = ["execute-api", "logs", "monitoring", "ecr.api", "ecr.dkr", "sts"]
}

# ------------------------------------------------------------- datos
variable "feature_flags" {
  description = "Estado INICIAL de las feature flags del ambiente. Después se cambian en caliente (Terraform ignora los cambios del item)."
  type        = map(bool)
}

variable "log_retention_days" {
  type = number
}

# ------------------------------------------------------------ cómputo
variable "enable_eks" {
  description = "Crear el clúster EKS (en Floci levanta un k3s real por ambiente)."
  type        = bool
  default     = true
}

variable "eks_version" {
  type    = string
  default = "1.31"
}

variable "eks_public_endpoint" {
  description = "Endpoint público del API server. true para poder usar kubectl desde tu PC con Floci; en AWS real se dejaría false + bastión/VPN."
  type        = bool
  default     = true
}

variable "node_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "node_desired_size" {
  type = number
}

# ------------------------------------------------------- API Gateway
variable "gateway_node_port" {
  description = "NodePort del Service graphql-gateway en Kubernetes (destino del NLB interno)."
  type        = number
  default     = 30080
}

variable "api_integration_mode" {
  description = "vpc_link = API Gateway -> VPC Link -> NLB interno -> NodePort (arquitectura objetivo). http_proxy = API Gateway -> URL local (respaldo si Floci no enruta el VPC Link)."
  type        = string
  default     = "vpc_link"
  validation {
    condition     = contains(["vpc_link", "http_proxy"], var.api_integration_mode)
    error_message = "api_integration_mode debe ser vpc_link o http_proxy."
  }
}

variable "local_backend_url" {
  description = "URL usada solo con api_integration_mode = http_proxy (p. ej. el port-forward del gateway)."
  type        = string
  default     = "http://host.docker.internal:8081"
}
