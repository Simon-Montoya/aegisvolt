# Levanta test y luego prod. Lo invoca levantar_test_prod.cmd (salida -> logs\levantar-test-prod.log)
# Continue: el stderr de docker/terraform no debe cortar el script; los errores se detectan por $LASTEXITCODE
$ErrorActionPreference = "Continue"
try {
  & "$PSScriptRoot\up_env.ps1" -Env test -Tag v2
  & "$PSScriptRoot\up_env.ps1" -Env prod -Tag v2
  Write-Host "`n##### TODO OK: test y prod arriba #####"
} catch {
  Write-Host "`n##### FALLO: $($_.Exception.Message) #####"
  exit 1
}
