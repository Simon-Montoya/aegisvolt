# Terraform con el workspace correcto (evita aplicar el .tfvars de un ambiente en otro).
#   .\scripts\tf.ps1 dev init
#   .\scripts\tf.ps1 dev plan
#   .\scripts\tf.ps1 test apply
# Nota: en PowerShell 5.1 los argumentos "-opcion=valor.ext" deben ir completos entre comillas;
# si no, PowerShell los parte en el punto y Terraform recibe un argumento suelto.
param(
  [Parameter(Mandatory = $true)][ValidateSet("dev", "test", "prod")][string]$Env,
  [Parameter(Mandatory = $true)][ValidateSet("init", "plan", "apply", "output", "destroy")][string]$Action
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\aws_env.ps1"
Remove-Item Env:AWS_ENDPOINT_URL -ErrorAction SilentlyContinue  # Terraform usa el bloque endpoints del provider

function Invoke-Tf {
  & terraform @args
  if ($LASTEXITCODE -ne 0) { throw "terraform $($args -join ' ') falló (código $LASTEXITCODE)" }
}

Push-Location "$PSScriptRoot\..\infra"
try {
  if (-not (Test-Path ".terraform\terraform.tfstate") -or $Action -eq "init") {
    Invoke-Tf init -reconfigure "-backend-config=backend/floci-local.hcl"
  }
  Invoke-Tf workspace select -or-create $Env
  Write-Host "Workspace activo: $(terraform workspace show)" -ForegroundColor Cyan
  $varFile = "-var-file=env/$Env.tfvars"
  switch ($Action) {
    "plan"    { Invoke-Tf plan $varFile }
    "apply"   { Invoke-Tf apply $varFile }
    "output"  { Invoke-Tf output }
    "destroy" { if ($Env -eq "prod") { throw "No se destruye prod desde este script." }; Invoke-Tf destroy $varFile }
  }
} finally { Pop-Location }
