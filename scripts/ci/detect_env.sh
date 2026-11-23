#!/bin/sh
# -----------------------------------------------------------------------------
# Detección dinámica del ambiente destino (POSIX sh; se usa con `source` o `.`).
#
#   Pre-merge  (PR de GitHub o MR de GitLab): ambiente = rama DESTINO del PR.
#   Post-merge (push a dev/test/prod tras el merge): ambiente = rama actual.
#
# Exporta TARGET_BRANCH, SOURCE_BRANCH, TF_ENV y TF_VAR_FILE.
# -----------------------------------------------------------------------------

if [ "${AEGIS_PIPELINE_MODE:-}" = "pre_merge" ]; then
  # PR de GitHub reenviado por GitHub Actions
  TARGET_BRANCH="${PR_TARGET_BRANCH}"
  SOURCE_BRANCH="${PR_SOURCE_BRANCH}"
elif [ "${CI_PIPELINE_SOURCE:-}" = "external_pull_request_event" ]; then
  TARGET_BRANCH="${CI_EXTERNAL_PULL_REQUEST_TARGET_BRANCH_NAME}"
  SOURCE_BRANCH="${CI_EXTERNAL_PULL_REQUEST_SOURCE_BRANCH_NAME}"
elif [ "${CI_PIPELINE_SOURCE:-}" = "merge_request_event" ]; then
  TARGET_BRANCH="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}"
  SOURCE_BRANCH="${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME}"
else
  TARGET_BRANCH="${CI_COMMIT_BRANCH:-${CI_COMMIT_REF_NAME:-}}"
  SOURCE_BRANCH="${TARGET_BRANCH}"
fi

case "$TARGET_BRANCH" in
  dev | develop) TF_ENV="dev" ;;
  test) TF_ENV="test" ;;
  prod) TF_ENV="prod" ;;
  *)
    echo "❌ La rama destino '${TARGET_BRANCH}' no corresponde a ningún ambiente (dev | test | prod)." >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

TF_VAR_FILE="env/${TF_ENV}.tfvars"
export TARGET_BRANCH SOURCE_BRANCH TF_ENV TF_VAR_FILE
echo "🎯 Rama origen: ${SOURCE_BRANCH} | rama destino: ${TARGET_BRANCH} | workspace Terraform: ${TF_ENV}"
