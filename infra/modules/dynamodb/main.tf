# -----------------------------------------------------------------------------
# Database-per-Service: cada microservicio es dueño de UNA tabla, separada por
# ambiente. Ningún servicio lee la tabla de otro: se comunican por HTTP.
#
#   aegisvolt-reservations-<env>   station-reservation-service
#   aegisvolt-billing-<env>        billing-wallet-service
#   aegisvolt-dispatch-<env>       energy-dispatch-service
#   aegisvolt-sagas-<env>          orquestador de la SAGA (graphql-gateway)
#   aegisvolt-feature-flags-<env>  configuración dinámica del ambiente
# -----------------------------------------------------------------------------

variable "project" { type = string }
variable "environment" { type = string }
variable "feature_flags" { type = map(bool) }

locals {
  tables = {
    reservations    = { hash = "pk", range = "sk", owner = "station-reservation-service" }
    billing         = { hash = "pk", range = "sk", owner = "billing-wallet-service" }
    dispatch        = { hash = "pk", range = "sk", owner = "energy-dispatch-service" }
    sagas           = { hash = "saga_id", range = null, owner = "graphql-gateway" }
    "feature-flags" = { hash = "name", range = null, owner = "platform" }
  }

  is_prod = var.environment == "prod"

  flag_descriptions = {
    ENABLE_DYNAMIC_SURGE_PRICING    = "Tarifa dinámica (multiplicador) en horas pico"
    ENABLE_FAST_CHARGE_BOOST        = "Reserva prioritaria de ultra-carga (>= 150 kW)"
    SIMULATE_CHARGER_HARDWARE_FAULT = "Inyección de fallo físico del cargador (dispara el rollback de la SAGA)"
  }
}

resource "aws_dynamodb_table" "this" {
  for_each = local.tables

  name         = "${var.project}-${each.key}-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = each.value.hash
  range_key    = each.value.range

  attribute {
    name = each.value.hash
    type = "S"
  }

  dynamic "attribute" {
    for_each = each.value.range == null ? [] : [each.value.range]
    content {
      name = attribute.value
      type = "S"
    }
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  # Un apply equivocado jamás puede borrar las tablas de prod.
  deletion_protection_enabled = local.is_prod

  tags = {
    Name    = "${var.project}-${each.key}-${var.environment}"
    Service = each.value.owner
  }
}

# Valor INICIAL de cada flag. ignore_changes: los cambios que se hagan en
# caliente (frontend / scripts/flags.py) no son revertidos por el próximo apply.
resource "aws_dynamodb_table_item" "flag" {
  for_each = var.feature_flags

  table_name = aws_dynamodb_table.this["feature-flags"].name
  hash_key   = "name"

  item = jsonencode({
    name        = { S = each.key }
    enabled     = { BOOL = each.value }
    description = { S = lookup(local.flag_descriptions, each.key, each.key) }
    environment = { S = var.environment }
    updated_by  = { S = "terraform" }
  })

  lifecycle {
    ignore_changes = [item]
  }
}

output "table_names" {
  value = { for k, t in aws_dynamodb_table.this : k => t.name }
}

output "table_arns" {
  value = [for t in aws_dynamodb_table.this : t.arn]
}
