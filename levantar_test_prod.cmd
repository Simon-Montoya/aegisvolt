@echo off
REM Levanta los ambientes test y prod de AegisVolt (Terraform + EKS + imagenes + canary + smoke test).
REM La salida completa queda en logs\levantar-test-prod.log
cd /d "%~dp0"
if not exist logs mkdir logs
echo Iniciando... > logs\levantar-test-prod.log
start "AegisVolt - progreso" powershell -NoProfile -Command "Get-Content -Path 'logs\levantar-test-prod.log' -Wait -Tail 60"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\up_test_prod.ps1 >> logs\levantar-test-prod.log 2>&1
echo.
echo Proceso terminado. Revise logs\levantar-test-prod.log
pause
