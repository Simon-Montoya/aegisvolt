# Abre una ventana de port-forward por ambiente (dev 8081, test 8082, prod 8083).
#   .\scripts\port_forward_all.ps1
. "$PSScriptRoot\aws_env.ps1"
# Registrar los 3 contextos de kubectl UNO por UNO antes de abrir las ventanas
foreach ($e in "dev", "test", "prod") {
  kubectl config get-contexts "aegisvolt-$e-eks" *> $null
  if ($LASTEXITCODE -ne 0) { aws eks update-kubeconfig --name "aegisvolt-$e-eks" --alias "aegisvolt-$e-eks" | Out-Null }
}
foreach ($e in "dev", "test", "prod") {
  Start-Sleep -Milliseconds 500
  Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSScriptRoot\port_forward.ps1`"", $e
}
Write-Host "Se abrieron 3 ventanas (dev:8081, test:8082, prod:8083). Para detenerlas, ciérrelas."
