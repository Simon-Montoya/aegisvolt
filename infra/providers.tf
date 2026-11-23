provider "aws" {
  region = var.region

  # Con Floci (aws_endpoint != null) se omiten las validaciones contra AWS real.
  s3_use_path_style           = var.aws_endpoint != null
  skip_credentials_validation = var.aws_endpoint != null
  skip_metadata_api_check     = var.aws_endpoint != null
  skip_requesting_account_id  = var.aws_endpoint != null

  dynamic "endpoints" {
    for_each = var.aws_endpoint == null ? [] : [var.aws_endpoint]
    content {
      apigateway     = endpoints.value
      autoscaling    = endpoints.value
      cloudwatch     = endpoints.value
      cloudwatchlogs = endpoints.value
      dynamodb       = endpoints.value
      ec2            = endpoints.value
      ecr            = endpoints.value
      eks            = endpoints.value
      elbv2          = endpoints.value
      iam            = endpoints.value
      s3             = endpoints.value
      sts            = endpoints.value
    }
  }

  default_tags {
    tags = {
      Project     = var.project
      Environment = terraform.workspace
      ManagedBy   = "terraform"
      Repository  = "aegisvolt"
    }
  }
}
