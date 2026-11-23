# Expone el graphql-gateway del clúster de un ambiente en tu PC.
#   .\scripts\port_forward.ps1 dev     -> http://localhost:8081/graphql
#   .\scripts\port_forward.ps1 test    -> http://localhost:8082/graphql
#   .\scripts\port_forward.ps1 prod    -> http://localhost:8083/graphql
param([Parameter(Mandatory = $true)][ValidateSet("dev", "test", "prod")][string]$Env)

$ports = @{ dev = 8081; test = 8082; prod = 8083 }
$cluster = "aegisvolt-$Env-eks"
. "$PSScriptRoot\aws_env.ps1"

# Solo se registra el contexto si falta (varias ventanas a la vez corromperían ~/.kube/config)
kubectl config get-contexts $cluster *> $null
if ($LASTEXITCODE -ne 0) { aws eks update-kubeconfig --name $cluster --alias $cluster | Out-Null }
Write-Host "Gateway de $Env en http://localhost:$($ports[$Env])/graphql  (Ctrl+C para detener)" -ForegroundColor Cyan
# --address 0.0.0.0: API Gateway de Floci (modo http_proxy) también puede alcanzarlo vía host.docker.internal
kubectl --context $cluster -n aegisvolt port-forward svc/graphql-gateway "$($ports[$Env]):80" --address 0.0.0.0
