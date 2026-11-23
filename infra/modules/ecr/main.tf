# Un repositorio de imágenes por microservicio y ambiente: aegisvolt-<env>/<service>

variable "prefix" { type = string }
variable "services" { type = list(string) }

resource "aws_ecr_repository" "this" {
  for_each             = toset(var.services)
  name                 = "${var.prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE" # una etiqueta = una imagen; el canary no se puede "pisar"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Conservar las ultimas 15 imagenes"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 15 }
      action       = { type = "expire" }
    }]
  })
}

output "repository_urls" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}
