# Backend para ejecutar Terraform desde tu PC contra Floci (http://localhost:4566).
#   terraform init -backend-config=backend/floci-local.hcl
bucket               = "aegisvolt-tfstate"
key                  = "aegisvolt/terraform.tfstate"
workspace_key_prefix = "env:"
region               = "us-east-1"
dynamodb_table       = "aegisvolt-tf-locks"
encrypt              = true

access_key                  = "test"
secret_key                  = "test"
use_path_style              = true
skip_credentials_validation = true
skip_requesting_account_id  = true
skip_metadata_api_check     = true
skip_region_validation      = true

endpoints = {
  s3       = "http://localhost:4566"
  dynamodb = "http://localhost:4566"
  sts      = "http://localhost:4566"
  iam      = "http://localhost:4566"
}
