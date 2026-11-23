# GitHub (repositorio) + GitLab (CI/CD) — plan gratuito

El código vive en **GitHub**. El pipeline (`.gitlab-ci.yml`) corre en **GitLab Free** con el
runner de tu PC (donde vive Floci). Un workflow de **GitHub Actions**
(`.github/workflows/gitlab-ci.yml`) hace de puente:

```
PR en GitHub ──► Actions: copia el merge del PR a GitLab (rama gh-pr-N) ──► pipeline pre_merge
                          espera el resultado ──► check "gitlab-pipeline" verde/rojo en el PR
Merge en GitHub ─► push a dev|test|prod ─► Actions ─► pipeline post_merge (apply + canary)
```

El Ruleset de GitHub exige el check `gitlab-pipeline` en verde para habilitar el botón Merge.

## 1. Proyecto en GitLab

1. gitlab.com → **New project → Create blank project**. Nombre `aegisvolt`, visibilidad privada o pública, **sin README**.
2. Anota la ruta del proyecto (p. ej. `simon-montoya/aegisvolt`).
3. **Settings → CI/CD → General pipelines**: deja el timeout en 1 h.

## 2. Token de GitLab para GitHub Actions

GitLab → avatar → **Preferences → Access tokens → Add new token**:
nombre `github-actions-aegisvolt`, expiración 30 días, scopes **`api`** y **`write_repository`**. Copia el `glpat-...`.

## 3. Configurar GitHub

Repositorio → **Settings → Secrets and variables → Actions**:

- pestaña *Secrets* → **New repository secret**: `GITLAB_TOKEN` = `glpat-...`
- pestaña *Variables* → **New repository variable**: `GITLAB_PROJECT` = `simon-montoya/aegisvolt` (tu ruta)

## 4. Runner en tu PC

GitLab → proyecto → **Settings → CI/CD → Runners → New project runner**: tag `aegisvolt-local`,
desmarcar *Run untagged jobs* → **Create runner** → copiar el token `glrt-...`.

```powershell
docker compose --profile ci up -d gitlab-runner
docker exec -it aegisvolt-gitlab-runner gitlab-runner register --non-interactive --url https://gitlab.com --token glrt-REEMPLAZAR --executor docker --docker-image alpine:3.20 --docker-volumes /var/run/docker.sock:/var/run/docker.sock --docker-extra-hosts host.docker.internal:host-gateway
```

El runner debe aparecer *online* (verde) en la misma página.

## 5. Variables del pipeline en GitLab

Proyecto → **Settings → CI/CD → Variables → Add variable** (marcar *Masked*, **desmarcar *Protected***):

| Variable | Valor (de `.aegis-local.json`) |
|---|---|
| `FLOCI_IAM_ACCESS_KEY_ID` | `aws_access_key_id` |
| `FLOCI_IAM_SECRET_ACCESS_KEY` | `aws_secret_access_key` |

Son las credenciales del usuario IAM `aegisvolt-admin` de Floci: el EKS del emulador valida el
token de kubectl con IAM y rechaza `test`/`test`.

## 6. Reglas de rama en GitHub

```bash
bash scripts/github_protect_branches.sh Simon-Montoya/aegisvolt gitlab-pipeline
```

Prohíbe push directo, force-push y borrado en `dev`, `test` y `prod`, y exige PR con el check
`gitlab-pipeline` en verde. Sin bypass: ni el dueño del repo puede saltárselas.

```bash
git checkout test && git commit --allow-empty -m x && git push origin test   # rechazado: requiere PR
git push --force origin test                                                  # rechazado: non_fast_forward
```

## 7. Flujo de trabajo (promoción lineal)

```bash
git checkout dev && git pull
git checkout -b feature/tarifa-nocturna
# ... cambios ...
git push -u origin feature/tarifa-nocturna
gh pr create --base dev --head feature/tarifa-nocturna   # pre_merge: tests + plan del workspace dev
# Merge en GitHub → push a dev → post_merge: apply dev + canary en el clúster dev
gh pr create --base test --head dev                      # promoción dev → test
gh pr create --base prod --head test                     # promoción test → prod
```

Un PR `feature/* → test` o `dev → prod` falla en `validate-promotion` y no se puede fusionar.
En cada PR, la pestaña *Checks* → `gitlab-pipeline` → *Summary* tiene el enlace al pipeline de GitLab.
