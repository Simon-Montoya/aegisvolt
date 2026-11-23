#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Historial y restauración del terraform.tfstate de un workspace (S3 versionado).
#
#   scripts/tfstate_versions.sh list    dev
#   scripts/tfstate_versions.sh download dev <version_id>     # inspeccionar una versión
#   scripts/tfstate_versions.sh restore dev <version_id>      # volver a esa versión
#
# Restaurar = copiar la versión vieja ENCIMA de la actual. No se borra nada:
# la versión corrupta sigue en el historial (auditoría) y la restaurada pasa a
# ser la "latest". Después: terraform workspace select dev && terraform plan
# para verificar que el estado cuadra con la infraestructura.
# -----------------------------------------------------------------------------
set -euo pipefail

ACTION="${1:?list|download|restore}"
ENVIRONMENT="${2:?dev|test|prod}"
BUCKET="${STATE_BUCKET:-aegisvolt-tfstate}"
KEY="env:/${ENVIRONMENT}/aegisvolt/terraform.tfstate"
export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:-http://localhost:4566}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}" AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}" AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

case "$ACTION" in
  list)
    aws s3api get-bucket-versioning --bucket "$BUCKET"
    aws s3api list-object-versions --bucket "$BUCKET" --prefix "$KEY" \
      --query 'Versions[].{VersionId:VersionId,LastModified:LastModified,Size:Size,IsLatest:IsLatest}' --output table
    ;;
  download)
    VERSION="${3:?version_id}"
    aws s3api get-object --bucket "$BUCKET" --key "$KEY" --version-id "$VERSION" "tfstate-${ENVIRONMENT}-${VERSION}.json" >/dev/null
    echo "Guardado en tfstate-${ENVIRONMENT}-${VERSION}.json (serial: $(jq .serial "tfstate-${ENVIRONMENT}-${VERSION}.json"))"
    ;;
  restore)
    VERSION="${3:?version_id}"
    aws s3api copy-object --bucket "$BUCKET" --key "$KEY" --copy-source "${BUCKET}/${KEY}?versionId=${VERSION}"
    echo "✅ ${KEY} restaurado a la versión ${VERSION}. Verifique con: terraform workspace select ${ENVIRONMENT} && terraform plan -var-file=env/${ENVIRONMENT}.tfvars"
    ;;
  *) echo "Acción desconocida: $ACTION" >&2; exit 1 ;;
esac
