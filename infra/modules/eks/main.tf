# -----------------------------------------------------------------------------
# EKS por ambiente (en Floci "real mode" levanta un k3s por clúster).
# Los nodos viven en las subredes PRIVADAS: no tienen IP pública ni ruta a
# internet; alcanzan DynamoDB/S3/CloudWatch/ECR por los VPC Endpoints.
#
# Los permisos se declaran como políticas inline (mínimo privilegio y sin
# depender de que existan las políticas administradas de AWS en el emulador).
# -----------------------------------------------------------------------------

variable "prefix" { type = string }
variable "cluster_name" { type = string }
variable "kubernetes_version" { type = string }
variable "vpc_id" { type = string }
variable "vpc_cidr" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "public_endpoint" { type = bool }
variable "node_instance_type" { type = string }
variable "node_desired_size" { type = number }
variable "dynamodb_table_arns" { type = list(string) }
variable "log_group_arns" { type = list(string) }
variable "gateway_node_port" { type = number }

# ------------------------------------------------------------- IAM clúster
resource "aws_iam_role" "cluster" {
  name = "${var.prefix}-eks-cluster"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "eks.amazonaws.com" }, Action = ["sts:AssumeRole", "sts:TagSession"] }]
  })
}

resource "aws_iam_role_policy" "cluster" {
  name = "eks-cluster"
  role = aws_iam_role.cluster.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ec2:Describe*", "ec2:CreateNetworkInterface", "ec2:DeleteNetworkInterface", "ec2:CreateTags", "elasticloadbalancing:*", "logs:CreateLogGroup", "logs:PutLogEvents", "kms:DescribeKey"]
      Resource = "*"
    }]
  })
}

# --------------------------------------------------------------- IAM nodos
resource "aws_iam_role" "node" {
  name = "${var.prefix}-eks-node"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "node" {
  name = "eks-node-workloads"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "NodeBasics"
        Effect   = "Allow"
        Action   = ["ec2:Describe*", "ec2:AssignPrivateIpAddresses", "ec2:AttachNetworkInterface", "ec2:CreateNetworkInterface", "ec2:DeleteNetworkInterface", "eks:DescribeCluster"]
        Resource = "*"
      },
      {
        Sid      = "PullImages"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = "*"
      },
      {
        Sid      = "OwnTablesOnly"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:TransactWriteItems", "dynamodb:DescribeTable"]
        Resource = concat(var.dynamodb_table_arns, [for arn in var.dynamodb_table_arns : "${arn}/*"])
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup", "logs:DescribeLogStreams"]
        Resource = concat(var.log_group_arns, [for arn in var.log_group_arns : "${arn}:*"])
      },
      {
        Sid      = "Metrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      }
    ]
  })
}

# ----------------------------------------------------------------- red
resource "aws_security_group" "cluster" {
  name        = "${var.prefix}-eks-sg"
  description = "Plano de control y nodos de EKS"
  vpc_id      = var.vpc_id

  ingress {
    description = "API server (443) desde la VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  ingress {
    description = "NodePort del graphql-gateway desde el NLB interno (misma VPC)"
    from_port   = var.gateway_node_port
    to_port     = var.gateway_node_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  ingress {
    description = "Trafico entre nodos y pods"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Solo dentro de la VPC (VPC Endpoints); no hay ruta a internet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "S3 y DynamoDB via Gateway Endpoints (prefix lists)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --------------------------------------------------------------- clúster
resource "aws_eks_cluster" "this" {
  name     = var.cluster_name
  version  = var.kubernetes_version
  role_arn = aws_iam_role.cluster.arn

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    security_group_ids      = [aws_security_group.cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = var.public_endpoint
  }

  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }

  depends_on = [aws_iam_role_policy.cluster]
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.prefix}-workers"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = [var.node_instance_type]

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = 1
    max_size     = var.node_desired_size + 2
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    "aegisvolt/pool" = "workers"
  }

  depends_on = [aws_iam_role_policy.node]
}

output "cluster_name" { value = aws_eks_cluster.this.name }
output "cluster_endpoint" { value = aws_eks_cluster.this.endpoint }
output "cluster_security_group_id" { value = aws_security_group.cluster.id }
output "node_autoscaling_group_names" {
  value = flatten([for r in aws_eks_node_group.this.resources : [for g in r.autoscaling_groups : g.name]])
}
