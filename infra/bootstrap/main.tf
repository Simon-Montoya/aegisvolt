# -----------------------------------------------------------------------------
# BOOTSTRAP · se ejecuta UNA sola vez, antes que todo lo demás.
#
# Crea el bucket S3 donde viven TODOS los terraform.tfstate del proyecto, con
# versionamiento habilitado (backups históricos de cada estado), y la tabla
# DynamoDB de bloqueo para que dos pipelines no apliquen a la vez.
#
# Este módulo usa estado LOCAL a propósito (problema del huevo y la gallina:
# no se puede guardar en S3 el estado del bucket que todavía no existe).
#
#   cd infra/bootstrap
#   terraform init
#   terraform apply
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
}

variable "aws_endpoint" {
  description = "Endpoint de Floci. null = AWS real."
  type        = string
  default     = "http://localhost:4566"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "state_bucket" {
  type    = string
  default = "aegisvolt-tfstate"
}

variable "lock_table" {
  type    = string
  default = "aegisvolt-tf-locks"
}

provider "aws" {
  region                      = var.region
  s3_use_path_style           = var.aws_endpoint != null
  skip_credentials_validation = var.aws_endpoint != null
  skip_metadata_api_check     = var.aws_endpoint != null
  skip_requesting_account_id  = var.aws_endpoint != null

  dynamic "endpoints" {
    for_each = var.aws_endpoint == null ? [] : [var.aws_endpoint]
    content {
      s3       = endpoints.value
      dynamodb = endpoints.value
      sts      = endpoints.value
      iam      = endpoints.value
    }
  }

  default_tags {
    tags = {
      Project   = "aegisvolt"
      Component = "terraform-state"
      ManagedBy = "terraform"
    }
  }
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

# Versionamiento: cada `terraform apply` genera una nueva versión del objeto
# env:/<workspace>/aegisvolt/terraform.tfstate. Las anteriores quedan como backup.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Conserva las versiones viejas 90 días (auditoría / recuperación ante corrupción).
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "retain-noncurrent-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
  depends_on = [aws_s3_bucket_versioning.state]
}

resource "aws_dynamodb_table" "locks" {
  name         = var.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "versioning_status" {
  value = aws_s3_bucket_versioning.state.versioning_configuration[0].status
}

output "lock_table" {
  value = aws_dynamodb_table.locks.name
}
