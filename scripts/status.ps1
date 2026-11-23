# Reactiva y verifica AegisVolt después de apagar el PC o Docker Desktop.
#   .\scripts\status.ps1
# 1) Arranca Floci y los clústeres k3s si están detenidos
# 2) Verifica nodos y pods de dev, test y prod
# 3) Smoke test GraphQL dentro de cada clúster (servicios + DynamoDB)
$ErrorActionPreference = "Continue"
$root = (Resolve-Path "$PSScriptRoot\..").Path
. "$PSScriptRoot\aws_env.ps1"

Write-Host "== Docker ==" -ForegroundColor Cyan
docker info --format "Docker {{.ServerVersion}} OK" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop no está corriendo: ábralo, espere a que diga 'Engine running' y vuelva a ejecutar este script." -ForegroundColor Red; exit 1 }

Push-Location $root; docker compose up -d floci; Pop-Location
foreach ($i in 1..30) { try { Invoke-RestMethod http://localhost:4566/_floci/health -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 2 } }
Write-Host "Floci listo en http://localhost:4566"

$stopped = docker ps -a --filter "name=floci-aws-eks-" --filter "status=exited" --format "{{.Names}}"
foreach ($c in $stopped) { if ($c) { Write-Host "Arrancando $c"; docker start $c | Out-Null } }
docker ps --filter "name=floci" --format "table {{.Names}}\t{{.Status}}"

$results = @()
foreach ($envName in "dev", "test", "prod") {
  $ctx = "aegisvolt-$envName-eks"
  Write-Host "`n== $envName ==" -ForegroundColor Cyan
  $ok = $false
  foreach ($i in 1..24) {
    $ready = kubectl --context $ctx get nodes --no-headers 2>$null | Select-String " Ready "
    if ($ready) { $ok = $true; break }
    Start-Sleep 5
  }
  if (-not $ok) { Write-Host "El nodo de $envName no está Ready" -ForegroundColor Red; $results += "$envName : NODO NO LISTO"; continue }
  # Tras reiniciar Docker los pods tardan en volver: se espera a que TODOS los deployments estén listos
  foreach ($d in "station-reservation-service", "billing-wallet-service", "energy-dispatch-service", "graphql-gateway") {
    kubectl --context $ctx -n aegisvolt rollout status "deploy/$d-stable" --timeout=240s | Out-Null
  }
  kubectl --context $ctx -n aegisvolt wait --for=condition=Ready pod -l app=graphql-gateway --timeout=180s | Out-Null
  $running = (kubectl --context $ctx -n aegisvolt get pods --no-headers 2>$null | Select-String "Running").Count
  Write-Host "Pods Running: $running"
  $out = Get-Content "$root\scripts\ci\smoke_query.py" -Raw | kubectl --context $ctx -n aegisvolt exec -i deploy/graphql-gateway-stable -- python - $envName 2>&1
  if ($out -match "SMOKE TEST OK") { $results += "$envName : OK ($running pods)" }
  else {
    $results += "$envName : FALLA el smoke test"
    $out | Select-Object -Last 15 | ForEach-Object { Write-Host $_ }
  }
}
Write-Host "`n===== RESUMEN =====" -ForegroundColor Cyan
$results | ForEach-Object { Write-Host $_ }
Write-Host "`nFrontend: .\scripts\port_forward_all.ps1   y luego   cd frontend; npm run dev"
