# Levanta un ambiente completo de punta a punta (sin preguntas):
#   Terraform apply -> IP de Floci en la VPC -> kubeconfig -> seed -> imágenes -> canary deploy -> smoke test
#   .\scripts\up_env.ps1 -Env test -Tag v2
param(
  [Parameter(Mandatory = $true)][ValidateSet("dev", "test", "prod")][string]$Env,
  [string]$Tag = "v2"
)
# Continue: el stderr de docker/terraform no debe cortar el script; los errores se detectan por $LASTEXITCODE
$ErrorActionPreference = "Continue"
$root = (Resolve-Path "$PSScriptRoot\..").Path
. "$PSScriptRoot\aws_env.ps1"
$cluster = "aegisvolt-$Env-eks"
function Step($msg) { Write-Host ""; Write-Host "===== [$Env] $msg ($(Get-Date -Format HH:mm:ss)) =====" }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what falló (código $LASTEXITCODE)" } }

Step "1/7 terraform apply"
Push-Location "$root\infra"
try {
  $savedEndpoint = $env:AWS_ENDPOINT_URL
  Remove-Item Env:AWS_ENDPOINT_URL -ErrorAction SilentlyContinue
  terraform workspace select -or-create $Env; Check "terraform workspace"
  terraform apply -auto-approve -input=false -no-color "-var-file=env/$Env.tfvars"; Check "terraform apply"
  $vpcId = (terraform output -raw vpc_id); Check "terraform output"
  $env:AWS_ENDPOINT_URL = $savedEndpoint
} finally { Pop-Location }
Write-Host "VPC del ambiente: $vpcId"

Step "2/7 IP de Floci dentro de la VPC"
$nets = docker inspect floci --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}};{{end}}'
Write-Host "Redes de floci: $nets"
$entry = ($nets -split ';') | Where-Object { $_ -like "*$vpcId=*" } | Select-Object -First 1
$ip = if ($entry) { ($entry -split '=')[1] } else { "192.168.65.254" }
if (-not $entry) { Write-Host "AVISO: floci no está en la red de $vpcId; se usa el host de Docker Desktop ($ip)" }
$envFile = "$root\k8s\env\$Env.env"
$content = (Get-Content $envFile -Raw -Encoding UTF8) -replace '(?m)^K8S_AWS_ENDPOINT=.*$', "K8S_AWS_ENDPOINT=http://${ip}:4566"
[IO.File]::WriteAllText($envFile, ($content -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "K8S_AWS_ENDPOINT=http://${ip}:4566"

Step "3/7 kubeconfig"
aws eks update-kubeconfig --name $cluster --alias $cluster; Check "update-kubeconfig"
kubectl --context $cluster get nodes -o wide; Check "kubectl get nodes"

Step "4/7 datos semilla"
python "$root\scripts\aegis.py" seed --env $Env; Check "seed"

Step "5/7 imágenes -> ECR"
& "$PSScriptRoot\build_push.ps1" $Env $Tag

Step "6/7 despliegue canary"
& "$PSScriptRoot\deploy.ps1" $Env $Tag

Step "7/7 smoke test GraphQL dentro del clúster"
Get-Content "$root\scripts\ci\smoke_query.py" -Raw | kubectl --context $cluster -n aegisvolt exec -i deploy/graphql-gateway-stable -- python - $Env
Check "smoke test"
Write-Host ""
Write-Host "===== [$Env] LISTO ($(Get-Date -Format HH:mm:ss)) ====="
