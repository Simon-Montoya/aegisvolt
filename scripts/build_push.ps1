# Construye las 4 imágenes y las sube al ECR (Floci) del ambiente.
#   .\scripts\build_push.ps1 dev v1
param(
  [Parameter(Mandatory = $true)][ValidateSet("dev", "test", "prod")][string]$Env,
  [Parameter(Mandatory = $true)][string]$Tag
)
# Continue: el stderr de docker/terraform no debe cortar el script; los errores se detectan por $LASTEXITCODE
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\aws_env.ps1"
$root = Resolve-Path "$PSScriptRoot\.."

# Salidas de Terraform del ambiente -> tf-outputs-<env>.json (ASCII para que lo lean jq/Python)
Push-Location "$root\infra"
try {
  terraform workspace select $Env | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "No existe el workspace $Env" }
  terraform output -json | Out-File -Encoding ascii "$root\tf-outputs-$Env.json"
} finally { Pop-Location }

$repos = (Get-Content "$root\tf-outputs-$Env.json" -Raw | ConvertFrom-Json).ecr_repositories.value
$registry = ($repos.'graphql-gateway' -split '/')[0]
Write-Host "Registro ECR: $registry" -ForegroundColor Cyan
aws ecr get-login-password | docker login --username AWS --password-stdin $registry
if ($LASTEXITCODE -ne 0) { throw "docker login falló" }

$services = [ordered]@{
  "station-reservation" = "station-reservation-service"
  "billing-wallet"      = "billing-wallet-service"
  "energy-dispatch"     = "energy-dispatch-service"
  "graphql-gateway"     = "graphql-gateway"
}
Push-Location $root
try {
  foreach ($dir in $services.Keys) {
    $image = "$($repos.($services[$dir])):$Tag"
    Write-Host "`n==> $image" -ForegroundColor Cyan
    docker build -f "services/$dir/Dockerfile" --build-arg "APP_VERSION=$Tag" -t $image .
    if ($LASTEXITCODE -ne 0) { throw "docker build falló para $dir" }
    docker push $image
    if ($LASTEXITCODE -ne 0) { throw "docker push falló para $image" }
  }
} finally { Pop-Location }
Write-Host "`nImágenes publicadas. Verifique con: aws ecr list-images --repository-name aegisvolt-$Env/graphql-gateway" -ForegroundColor Green
