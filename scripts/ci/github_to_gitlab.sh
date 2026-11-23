#!/usr/bin/env bash
# Sincroniza el commit con GitLab y ejecuta allí el pipeline; termina con el estado del pipeline.
set -euo pipefail
: "${GITLAB_TOKEN:?Falta el secret GITLAB_TOKEN en GitHub}"
: "${GITLAB_PROJECT:?Falta la variable GITLAB_PROJECT en GitHub}"
GITLAB_URL="${GITLAB_URL:-https://gitlab.com}"
PROJECT_ENC="$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["GITLAB_PROJECT"], safe=""))')"
API="${GITLAB_URL}/api/v4/projects/${PROJECT_ENC}"
REMOTE="https://oauth2:${GITLAB_TOKEN}@${GITLAB_URL#https://}/${GITLAB_PROJECT}.git"

if [[ "${GITHUB_EVENT_NAME}" == "pull_request" ]]; then
  MODE="pre_merge"; REF="gh-pr-${PR_NUMBER}"
  SOURCE="${GITHUB_HEAD_REF}"; TARGET="${GITHUB_BASE_REF}"
  PUSH_FLAGS=(--force)            # rama temporal del PR (contiene el merge PR + destino)
else
  MODE="post_merge"; REF="${GITHUB_REF_NAME}"
  SOURCE="${REF}"; TARGET="${REF}"
  PUSH_FLAGS=()
fi
echo "Modo: ${MODE} | origen: ${SOURCE} | destino: ${TARGET} | ref GitLab: ${REF}"

# -o ci.skip: GitLab no dispara un pipeline por el push; lo lanzamos nosotros con las variables.
git push "${PUSH_FLAGS[@]}" -o ci.skip "${REMOTE}" "HEAD:refs/heads/${REF}"

PAYLOAD="$(jq -n --arg ref "$REF" --arg mode "$MODE" --arg src "$SOURCE" --arg tgt "$TARGET" --arg sha "$GITHUB_SHA" '{
  ref: $ref,
  variables: [
    {key: "AEGIS_PIPELINE_MODE", value: $mode},
    {key: "PR_SOURCE_BRANCH",    value: $src},
    {key: "PR_TARGET_BRANCH",    value: $tgt},
    {key: "GITHUB_COMMIT_SHA",   value: $sha}
  ]}')"
PIPELINE="$(curl -sS --fail-with-body -X POST -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" -H "Content-Type: application/json" -d "$PAYLOAD" "${API}/pipeline")"
ID="$(jq -r .id <<<"$PIPELINE")"; URL="$(jq -r .web_url <<<"$PIPELINE")"
echo "Pipeline GitLab #${ID}: ${URL}"
echo "### Pipeline de GitLab (${MODE}: ${SOURCE} → ${TARGET})" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "[#${ID}](${URL})" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

while true; do
  sleep 20
  STATUS="$(curl -sS -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" "${API}/pipelines/${ID}" | jq -r .status)"
  echo "$(date +%H:%M:%S) estado: ${STATUS}"
  case "$STATUS" in
    success) echo "✅ Pipeline de GitLab en verde: ${URL}"; exit 0 ;;
    failed | canceled | skipped)
      echo "❌ Pipeline de GitLab terminó en ${STATUS}: ${URL}"
      curl -sS -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" "${API}/pipelines/${ID}/jobs?scope[]=failed" \
        | jq -r '.[] | "  - job fallido: \(.stage)/\(.name) → \(.web_url)"'
      exit 1 ;;
  esac
done
