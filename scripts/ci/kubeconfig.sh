#!/usr/bin/env bash
# Configura kubectl hacia el clúster EKS (k3s de Floci) del ambiente.
#   scripts/ci/kubeconfig.sh <env>
set -euo pipefail
ENVIRONMENT="${1:?ambiente}"
CLUSTER="aegisvolt-${ENVIRONMENT}-eks"

aws eks update-kubeconfig --name "$CLUSTER" --region "${AWS_DEFAULT_REGION:-us-east-1}" --alias "$CLUSTER"
kubectl config use-context "$CLUSTER" >/dev/null

# Floci anuncia el API server como https://localhost:<puerto>. Dentro de un
# contenedor del runner, "localhost" no es tu PC: se reescribe a
# host.docker.internal y se omite la verificación TLS del certificado
# autofirmado del k3s local (solo aplica al emulador).
if [[ "${KUBE_HOST_REWRITE:-true}" == "true" ]]; then
  ENTRY="$(kubectl config view --minify -o jsonpath='{.clusters[0].name}')"
  SERVER="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
  NEW_SERVER="${SERVER/localhost/host.docker.internal}"
  NEW_SERVER="${NEW_SERVER/127.0.0.1/host.docker.internal}"
  kubectl config unset "clusters.${ENTRY}.certificate-authority-data" >/dev/null || true
  kubectl config set-cluster "$ENTRY" --server="$NEW_SERVER" --insecure-skip-tls-verify=true >/dev/null
  echo "API server: ${NEW_SERVER}"
fi

kubectl get nodes -o wide
