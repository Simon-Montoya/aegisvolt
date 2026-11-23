#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Crea en GitHub un Ruleset que protege dev, test y prod:
#   * non_fast_forward  -> prohíbe git push --force
#   * deletion          -> prohíbe borrar las ramas
#   * pull_request      -> prohíbe el push directo: todo entra por PR
#   * required_status_checks -> el pipeline de GitLab debe estar en verde
#     (unit, integration, e2e, terraform-plan, validate-promotion) para habilitar Merge
# Sin bypass_actors: ni siquiera el dueño del repo puede saltarse las reglas.
#
#   gh auth login
#   scripts/github_protect_branches.sh <owner>/<repo> [contexto_del_check]
#
# El contexto del check es el nombre con el que GitLab reporta el estado en
# GitHub (aparece en la pestaña Checks del primer PR, p. ej. "ci/gitlab/gitlab.com").
# Requisito: repo público, o privado con GitHub Pro/Team (Student Developer Pack).
# -----------------------------------------------------------------------------
set -euo pipefail
REPO="${1:?owner/repo}"
CHECK="${2:-ci/gitlab/gitlab.com}"

payload=$(cat <<JSON
{
  "name": "aegisvolt-protected-environments",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": { "ref_name": { "include": ["refs/heads/dev", "refs/heads/test", "refs/heads/prod"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
    } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [ { "context": "${CHECK}" } ]
    } }
  ]
}
JSON
)

existing=$(gh api "repos/${REPO}/rulesets" --jq '.[] | select(.name=="aegisvolt-protected-environments") | .id' || true)
if [[ -n "$existing" ]]; then
  echo "$payload" | gh api -X PUT "repos/${REPO}/rulesets/${existing}" --input - >/dev/null
  echo "Ruleset actualizado (id ${existing})"
else
  echo "$payload" | gh api -X POST "repos/${REPO}/rulesets" --input - >/dev/null
  echo "Ruleset creado"
fi
gh api "repos/${REPO}/rulesets" --jq '.[] | {id, name, enforcement}'
echo "Prueba: git push --force origin test   -> debe ser rechazado (GH013: Repository rule violations)"
