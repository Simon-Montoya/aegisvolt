# Variables de entorno para hablar con Floci. Se carga con: . "$PSScriptRoot\aws_env.ps1"
# EKS de Floci valida el token de kubectl con IAM y rechaza el par test/test: por eso se usa
# la access key del usuario IAM aegisvolt-admin guardada en .aegis-local.json (ignorado por Git).
$env:AWS_ENDPOINT_URL = "http://localhost:4566"
$env:AWS_DEFAULT_REGION = "us-east-1"
$localCreds = Join-Path (Resolve-Path "$PSScriptRoot\..") ".aegis-local.json"
if (Test-Path $localCreds) {
  $c = Get-Content $localCreds -Raw | ConvertFrom-Json
  $env:AWS_ACCESS_KEY_ID = $c.aws_access_key_id
  $env:AWS_SECRET_ACCESS_KEY = $c.aws_secret_access_key
} else {
  Write-Warning "No existe .aegis-local.json: se usa test/test (kubectl contra EKS de Floci fallará)."
  $env:AWS_ACCESS_KEY_ID = "test"; $env:AWS_SECRET_ACCESS_KEY = "test"
}
