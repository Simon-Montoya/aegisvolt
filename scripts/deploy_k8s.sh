#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Despliega AegisVolt en el clúster EKS del ambiente con estrategia Canary.
#
#   scripts/deploy_k8s.sh <env> <image_tag> [tf_outputs.json]
#
# Orden: microservicios de dominio primero, gateway al final (el gateway es
# quien orquesta la SAGA; se actualiza cuando sus dependencias ya están listas).
# -----------------------------------------------------------------------------
set -euo pipefail

ENVIRONMENT="${1:?ambiente}"
TAG="${2:?tag de imagen}"
OUTPUTS="${3:-tf-outputs.json}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

set -a
# shellcheck disable=SC1090
source "$ROOT/k8s/env/${ENVIRONMENT}.env"
set +a

# ---------------------------------------------------------------------------
# Endpoint de Floci visto DESDE LOS PODS. Se prueban varias rutas con una consulta
# real a DynamoDB hecha dentro del clúster y se usa la primera que responde:
#   1. IP de Floci en la red de la VPC del ambiente (10.x.0.2): el tráfico no sale de la VPC
#   2. host de Docker Desktop (192.168.65.254 -> puerto 4566 publicado por Floci)
#   3. IP de Floci en la red aegisvolt-net
#   4. el valor de k8s/env/<env>.env
# Docker puede cambiar estas rutas al reiniciarse, por eso se re-evalúa en cada despliegue.
# ---------------------------------------------------------------------------
CANDIDATES=()
if command -v docker >/dev/null 2>&1; then
  NETS="$(docker inspect floci --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}' 2>/dev/null | tr ' ' '\n' || true)"
  VPC_ID="$( [[ -f "$OUTPUTS" ]] && jq -r '.vpc_id.value // empty' "$OUTPUTS" || true)"
  [[ -n "$VPC_ID" ]] && VPC_IP="$(grep -- "${VPC_ID}=" <<<"$NETS" | cut -d= -f2 || true)" && [[ -n "$VPC_IP" ]] && CANDIDATES+=("http://${VPC_IP}:4566")
  CANDIDATES+=("http://192.168.65.254:4566")
  NET_IP="$(grep -- "^aegisvolt-net=" <<<"$NETS" | cut -d= -f2 || true)"
  [[ -n "$NET_IP" ]] && CANDIDATES+=("http://${NET_IP}:4566")
fi
CANDIDATES+=("$K8S_AWS_ENDPOINT")

PROBE_DEPLOY=""
for d in graphql-gateway-stable station-reservation-service-stable; do
  kubectl -n aegisvolt get deploy "$d" >/dev/null 2>&1 && PROBE_DEPLOY="$d" && break
done

probe() { # $1 = endpoint; éxito si DynamoDB responde desde un pod en < 10 s
  timeout 20 kubectl -n aegisvolt exec "deploy/${PROBE_DEPLOY}" -- python -c "
import boto3
from botocore.config import Config
c = boto3.client('dynamodb', endpoint_url='$1', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test',
                 config=Config(connect_timeout=3, read_timeout=6, retries={'max_attempts': 1}))
c.list_tables(Limit=1)
" >/dev/null 2>&1
}

if [[ -n "$PROBE_DEPLOY" ]]; then
  SELECTED=""
  for ep in "${CANDIDATES[@]}"; do
    if probe "$ep"; then echo "✔ DynamoDB responde desde los pods vía ${ep}"; SELECTED="$ep"; break
    else echo "✘ sin respuesta desde los pods vía ${ep}"; fi
  done
  if [[ -n "$SELECTED" ]]; then K8S_AWS_ENDPOINT="$SELECTED"
  else echo "AVISO: ninguna ruta respondió; se mantiene ${K8S_AWS_ENDPOINT}"; fi
else
  # Primer despliegue: aún no hay pods para probar; se usa la primera candidata.
  K8S_AWS_ENDPOINT="${CANDIDATES[0]}"
fi
echo "Endpoint de Floci para los pods: ${K8S_AWS_ENDPOINT}"
export K8S_AWS_ENDPOINT

CONFIG_CHANGED=false
for f in "$ROOT"/k8s/templates/[0-9]*.yaml; do
  out="$(envsubst '${AEGIS_ENV} ${K8S_AWS_ENDPOINT} ${SURGE_FORCE_PEAK}' < "$f" | kubectl apply -f -)"
  echo "$out"
  grep -q "configmap/aegisvolt-config configured" <<<"$out" && CONFIG_CHANGED=true
done

# Si cambió la configuración (p. ej. la IP de Floci), los pods existentes deben releerla.
if [[ "$CONFIG_CHANGED" == "true" ]]; then
  echo "La configuración cambió: reiniciando los pods stable para que la tomen"
  for svc in station-reservation-service billing-wallet-service energy-dispatch-service graphql-gateway; do
    kubectl -n aegisvolt get deploy "${svc}-stable" >/dev/null 2>&1 || continue
    kubectl -n aegisvolt rollout restart "deploy/${svc}-stable"
  done
  for svc in station-reservation-service billing-wallet-service energy-dispatch-service graphql-gateway; do
    kubectl -n aegisvolt get deploy "${svc}-stable" >/dev/null 2>&1 || continue
    kubectl -n aegisvolt rollout status "deploy/${svc}-stable" --timeout=240s
  done
fi

for service in station-reservation-service billing-wallet-service energy-dispatch-service graphql-gateway; do
  repo="$(jq -r --arg s "$service" '.ecr_repositories.value[$s]' "$OUTPUTS")"
  bash "$ROOT/scripts/canary_deploy.sh" "$ENVIRONMENT" "$service" "${repo}:${TAG}" "$TAG"
done

kubectl -n aegisvolt get deploy,svc,pods -o wide
