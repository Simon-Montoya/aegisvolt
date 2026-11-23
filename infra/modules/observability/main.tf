# -----------------------------------------------------------------------------
# CloudWatch: un grupo de logs por microservicio y ambiente
#   /aegisvolt/<env>/<service>
# + filtros de métricas de errores (los usa el análisis del Canary Deployment)
# + alarma de errores del track canary.
# -----------------------------------------------------------------------------

variable "project" { type = string }
variable "environment" { type = string }
variable "services" { type = list(string) }
variable "retention_days" { type = number }

locals {
  namespace = "AegisVolt/${var.environment}"
  extra     = ["api-gateway", "observability"]
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(concat(var.services, local.extra))
  name              = "/${var.project}/${var.environment}/${each.key}"
  retention_in_days = var.retention_days

  tags = { Service = each.key }
}

# Cuenta cada línea JSON con level = ERROR.
resource "aws_cloudwatch_log_metric_filter" "errors" {
  for_each       = toset(var.services)
  name           = "${each.key}-errors"
  log_group_name = aws_cloudwatch_log_group.service[each.key].name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name          = "${each.key}-errors"
    namespace     = local.namespace
    value         = "1"
    default_value = "0"
  }
}

# Errores producidos SOLO por los pods de la versión canary.
resource "aws_cloudwatch_log_metric_filter" "canary_errors" {
  for_each       = toset(var.services)
  name           = "${each.key}-canary-errors"
  log_group_name = aws_cloudwatch_log_group.service[each.key].name
  pattern        = "{ ($.level = \"ERROR\") && ($.track = \"canary\") }"

  metric_transformation {
    name          = "${each.key}-canary-errors"
    namespace     = local.namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "canary_errors" {
  for_each            = toset(var.services)
  alarm_name          = "${var.project}-${var.environment}-${each.key}-canary-errors"
  alarm_description   = "Errores de la versión canary de ${each.key} en ${var.environment}. Si entra en ALARM el pipeline hace rollback."
  namespace           = local.namespace
  metric_name         = "${each.key}-canary-errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}

output "log_group_names" {
  value = { for k, g in aws_cloudwatch_log_group.service : k => g.name }
}

output "log_group_arns" {
  value = [for g in aws_cloudwatch_log_group.service : g.arn]
}

output "api_gateway_log_group_arn" {
  value = aws_cloudwatch_log_group.service["api-gateway"].arn
}
