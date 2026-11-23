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

# IP de Floci dentro de la red de la VPC del ambiente. Docker puede reasignarla al
# reiniciar, así que se detecta en cada despliegue (si hay acceso al socket de Docker).
if command -v docker >/dev/null 2>&1 && [[ -f "$OUTPUTS" ]]; then
  VPC_ID="$(jq -r '.vpc_id.value // empty' "$OUTPUTS")"
  FLOCI_IP="$(docker inspect floci --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}' 2>/dev/null \
    | tr ' ' '\n' | grep -- "${VPC_ID}=" | cut -d= -f2 || true)"
  if [[ -n "$VPC_ID" && -n "$FLOCI_IP" ]]; then
    K8S_AWS_ENDPOINT="http://${FLOCI_IP}:4566"
    echo "Floci en la VPC ${VPC_ID}: ${K8S_AWS_ENDPOINT}"
  else
    echo "AVISO: no se detectó la IP de Floci en la VPC; se usa ${K8S_AWS_ENDPOINT} (k8s/env/${ENVIRONMENT}.env)"
  fi
fi
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
