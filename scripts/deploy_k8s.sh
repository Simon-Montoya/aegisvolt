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

for f in "$ROOT"/k8s/templates/[0-9]*.yaml; do
  envsubst '${AEGIS_ENV} ${K8S_AWS_ENDPOINT} ${SURGE_FORCE_PEAK}' < "$f" | kubectl apply -f -
done

for service in station-reservation-service billing-wallet-service energy-dispatch-service graphql-gateway; do
  repo="$(jq -r --arg s "$service" '.ecr_repositories.value[$s]' "$OUTPUTS")"
  bash "$ROOT/scripts/canary_deploy.sh" "$ENVIRONMENT" "$service" "${repo}:${TAG}" "$TAG"
done

kubectl -n aegisvolt get deploy,svc,pods -o wide
