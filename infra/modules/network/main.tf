# -----------------------------------------------------------------------------
# Red del ambiente: VPC dedicada, 2 subredes públicas + 2 privadas, y VPC
# Endpoints para que el tráfico hacia AWS NUNCA salga a internet.
#
#  * Las subredes privadas NO tienen ruta 0.0.0.0/0 (ni NAT ni IGW). Los pods y
#    nodos de EKS solo pueden hablar con:
#      - la propia VPC (ruta local),
#      - S3 y DynamoDB a través de Gateway Endpoints (rutas a prefix lists en
#        la tabla de enrutamiento privada),
#      - API Gateway, CloudWatch Logs/Metrics, ECR y STS a través de Interface
#        Endpoints (ENIs privadas en las subredes, con DNS privado).
#  * Las subredes públicas solo existen para recursos que deban recibir
#    tráfico de internet (no se usan en el flujo interno).
# -----------------------------------------------------------------------------

variable "prefix" { type = string }
variable "environment" { type = string }
variable "region" { type = string }
variable "vpc_cidr" { type = string }
variable "availability_zones" { type = list(string) }
variable "interface_endpoints" { type = list(string) }

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true # requisito del DNS privado de los Interface Endpoints
  enable_dns_hostnames = true

  tags = { Name = "${var.prefix}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.prefix}-igw" }
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.this.id
  availability_zone       = var.availability_zones[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index) # x.x.0.0/24, x.x.1.0/24
  map_public_ip_on_launch = true

  tags = {
    Name                     = "${var.prefix}-public-${count.index + 1}"
    Tier                     = "public"
    "kubernetes.io/role/elb" = "1"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.this.id
  availability_zone = var.availability_zones[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10) # x.x.10.0/24, x.x.11.0/24

  tags = {
    Name                              = "${var.prefix}-private-${count.index + 1}"
    Tier                              = "private"
    "kubernetes.io/role/internal-elb" = "1"
  }
}

# ---------------------------------------------------------------- rutas
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.prefix}-rt-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Tabla privada: solo ruta local + las rutas que inyectan los Gateway Endpoints.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.prefix}-rt-private" }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ------------------------------------------------------ security groups
resource "aws_security_group" "endpoints" {
  name        = "${var.prefix}-vpce-sg"
  description = "Interface VPC Endpoints: HTTPS solo desde la VPC"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS desde la VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "Respuestas dentro de la VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = { Name = "${var.prefix}-vpce-sg" }
}

# --------------------------------------------- Gateway Endpoints (S3, DynamoDB)
resource "aws_vpc_endpoint" "gateway" {
  for_each = toset(["s3", "dynamodb"])

  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  # Política: solo se permite acceder a recursos del proyecto.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "OnlyAegisVoltResources"
      Effect    = "Allow"
      Principal = "*"
      Action    = "*"
      Resource = each.key == "s3" ? [
        "arn:aws:s3:::aegisvolt-*",
        "arn:aws:s3:::aegisvolt-*/*",
        ] : [
        "arn:aws:dynamodb:${var.region}:*:table/aegisvolt-*-${var.environment}",
        "arn:aws:dynamodb:${var.region}:*:table/aegisvolt-*-${var.environment}/*",
      ]
    }]
  })

  tags = { Name = "${var.prefix}-vpce-${each.key}" }
}

# ------------------------------ Interface Endpoints (PrivateLink, DNS privado)
resource "aws_vpc_endpoint" "interface" {
  for_each = toset(var.interface_endpoints)

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${var.prefix}-vpce-${replace(each.key, ".", "-")}" }
}

output "vpc_id" { value = aws_vpc.this.id }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "private_route_table_id" { value = aws_route_table.private.id }
output "endpoint_security_group_id" { value = aws_security_group.endpoints.id }
output "gateway_endpoint_ids" { value = { for k, v in aws_vpc_endpoint.gateway : k => v.id } }
output "interface_endpoint_ids" { value = { for k, v in aws_vpc_endpoint.interface : k => v.id } }
