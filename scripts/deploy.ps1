# Despliega en el EKS (k3s de Floci) del ambiente con la estrategia Canary, usando el
# mismo contenedor y los mismos scripts que el pipeline de GitLab.
#   .\scripts\deploy.ps1 dev v1
#   .\scripts\deploy.ps1 dev v2 -BadRelease     # demo de rollback automático
param(
  [Parameter(Mandatory = $true)][ValidateSet("dev", "test", "prod")][string]$Env,
  [Parameter(Mandatory = $true)][string]$Tag,
  [switch]$BadRelease
)
# Continue: el stderr de docker/terraform no debe cortar el script; los errores se detectan por $LASTEXITCODE
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\aws_env.ps1"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$outputs = "tf-outputs-$Env.json"
if (-not (Test-Path "$root\$outputs")) { throw "Falta $outputs. Ejecute primero .\scripts\build_push.ps1 $Env $Tag" }
$bad = if ($BadRelease) { "true" } else { "false" }

docker run --rm `
  -v "${root}:/repo" -w /repo `
  -e AWS_ENDPOINT_URL=http://host.docker.internal:4566 `
  -e AWS_ACCESS_KEY_ID=$env:AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY=$env:AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION=us-east-1 `
  -e SIMULATE_BAD_RELEASE=$bad `
  alpine/k8s:1.31.4 `
  bash -c "apk add --no-cache gettext jq >/dev/null 2>&1; bash scripts/ci/kubeconfig.sh $Env && bash scripts/deploy_k8s.sh $Env $Tag $outputs"
if ($LASTEXITCODE -ne 0) { throw "El despliegue falló (código $LASTEXITCODE)" }
