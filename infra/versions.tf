terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # Estado remoto en S3 (bucket creado por infra/bootstrap, con versionamiento).
  # La configuración concreta (endpoint de Floci, bucket, tabla de locks) se pasa
  # en `terraform init -backend-config=backend/<archivo>.hcl`.
  #
  # Con workspace_key_prefix = "env:" cada workspace queda aislado en su propia key:
  #   s3://aegisvolt-tfstate/env:/dev/aegisvolt/terraform.tfstate
  #   s3://aegisvolt-tfstate/env:/test/aegisvolt/terraform.tfstate
  #   s3://aegisvolt-tfstate/env:/prod/aegisvolt/terraform.tfstate
  backend "s3" {}
}
