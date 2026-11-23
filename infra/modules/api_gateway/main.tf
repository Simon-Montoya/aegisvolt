# -----------------------------------------------------------------------------
# API Gateway PRIVADO (REST) delante de la capa GraphQL.
#
#  Cliente en la VPC --HTTPS--> Interface VPCE execute-api (ENI privada)
#     --> API Gateway (endpoint PRIVATE; la resource policy solo acepta
#         peticiones cuyo aws:SourceVpce sea el VPCE de este ambiente)
#     --> VPC Link --> NLB interno --> NodePort --> pod graphql-gateway
#
# En modo http_proxy (respaldo para el emulador) la integración apunta a una
# URL local en vez del VPC Link.
# -----------------------------------------------------------------------------

variable "prefix" { type = string }
variable "environment" { type = string }
variable "execute_api_vpce_id" { type = string }
variable "integration_mode" { type = string }
variable "nlb_arn" {
  type    = string
  default = null
}
variable "nlb_dns_name" {
  type    = string
  default = null
}
variable "local_backend_url" { type = string }

locals {
  use_vpc_link = var.integration_mode == "vpc_link"
  backend_base = local.use_vpc_link ? "http://${var.nlb_dns_name}" : var.local_backend_url
}

resource "aws_api_gateway_rest_api" "this" {
  name        = "${var.prefix}-api"
  description = "AegisVolt GraphQL API (${var.environment})"

  endpoint_configuration {
    types            = ["PRIVATE"]
    vpc_endpoint_ids = [var.execute_api_vpce_id]
  }
}

resource "aws_api_gateway_rest_api_policy" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowFromOwnVpce"
        Effect    = "Allow"
        Principal = "*"
        Action    = "execute-api:Invoke"
        Resource  = "${aws_api_gateway_rest_api.this.execution_arn}/*"
      },
      {
        Sid       = "DenyOutsideVpce"
        Effect    = "Deny"
        Principal = "*"
        Action    = "execute-api:Invoke"
        Resource  = "${aws_api_gateway_rest_api.this.execution_arn}/*"
        Condition = { StringNotEquals = { "aws:SourceVpce" = var.execute_api_vpce_id } }
      }
    ]
  })
}

resource "aws_api_gateway_vpc_link" "this" {
  count       = local.use_vpc_link ? 1 : 0
  name        = "${var.prefix}-vpc-link"
  target_arns = [var.nlb_arn]
}

resource "aws_api_gateway_resource" "graphql" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "graphql"
}

resource "aws_api_gateway_resource" "health" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "health"
}

resource "aws_api_gateway_method" "this" {
  for_each = {
    graphql = aws_api_gateway_resource.graphql.id
    health  = aws_api_gateway_resource.health.id
  }
  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = each.value
  http_method   = "ANY"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "this" {
  for_each = aws_api_gateway_method.this

  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = each.value.resource_id
  http_method             = each.value.http_method
  type                    = "HTTP_PROXY"
  integration_http_method = "ANY"
  uri                     = "${local.backend_base}/${each.key}"
  connection_type         = local.use_vpc_link ? "VPC_LINK" : "INTERNET"
  connection_id           = local.use_vpc_link ? aws_api_gateway_vpc_link.this[0].id : null
  timeout_milliseconds    = 29000
}

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id

  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_integration.this,
      aws_api_gateway_rest_api_policy.this.policy,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "this" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  deployment_id = aws_api_gateway_deployment.this.id
  stage_name    = var.environment

  variables = {
    environment = var.environment
  }
}

output "summary" {
  value = {
    rest_api_id      = aws_api_gateway_rest_api.this.id
    stage            = aws_api_gateway_stage.this.stage_name
    invoke_url       = aws_api_gateway_stage.this.invoke_url
    integration_mode = var.integration_mode
    vpc_link_id      = local.use_vpc_link ? aws_api_gateway_vpc_link.this[0].id : null
    private_url      = "https://${aws_api_gateway_rest_api.this.id}-${var.execute_api_vpce_id}.execute-api.amazonaws.com/${var.environment}/graphql"
  }
}
