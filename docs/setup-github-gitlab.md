# GitHub (repositorio) + GitLab (CI/CD)

El código vive en GitHub. GitLab ejecuta el pipeline mediante **CI/CD for external repositories**:
GitLab replica el repo, recibe los webhooks de push y pull request, corre `.gitlab-ci.yml` y
**reporta el estado al PR de GitHub**. El Ruleset de GitHub exige ese estado en verde para hacer merge.

> **Licencia:** "CI/CD for external repositories" es una función de GitLab **Premium/Ultimate**. Opciones gratuitas: la prueba de 30 días de Ultimate (alcanza para las 2 semanas del proyecto) o el programa *GitLab for Education*. Activa la prueba **antes** de conectar el repo.

## 1. Repositorio en GitHub

```bash
cd aegisvolt
terraform fmt -recursive infra    # el job terraform-validate exige formato canónico
git init -b dev
git add . && git commit -m "feat: AegisVolt inicial"
gh repo create <usuario>/aegisvolt --public --source . --push   # o créalo en la web y haz git remote add + push
git push origin dev:test dev:prod                                # las 3 ramas de ambiente parten del mismo commit
```

Las reglas de rama (Rulesets) son gratis en repos **públicos**. En un repo privado necesitas GitHub Pro, que es gratis con el GitHub Student Developer Pack.

## 2. Proyecto en GitLab conectado a GitHub

1. GitLab → **New project → Run CI/CD for external repository → GitHub**.
2. Autoriza con un token de GitHub (scope `repo`) y elige `aegisvolt`.
3. GitLab crea el proyecto con *pull mirroring* y registra los webhooks de push y pull_request en GitHub.
4. Settings → CI/CD → **Variables**: crea `FLOCI_IAM_ACCESS_KEY_ID` y `FLOCI_IAM_SECRET_ACCESS_KEY` (marca *Masked*) con los valores de `.aegis-local.json`. Corresponden al usuario IAM `aegisvolt-admin` creado en Floci: el EKS del emulador valida el token de kubectl con IAM y rechaza `test`/`test`. Los demás jobs siguen usando `test`/`test`.

## 3. Runner en tu PC (donde vive Floci)

Todos los jobs llevan el tag `aegisvolt-local`: necesitan llegar a Floci (`host.docker.internal:4566`) y al Docker de tu PC.

1. GitLab → Settings → CI/CD → Runners → **New project runner**: tag `aegisvolt-local` y "Run untagged jobs" desactivado. Copia el token `glrt-...`.
2. Levanta el runner y regístralo:

```powershell
docker compose --profile ci up -d gitlab-runner
docker exec -it aegisvolt-gitlab-runner gitlab-runner register `
  --non-interactive --url https://gitlab.com --token glrt-XXXXXXXX `
  --executor docker --docker-image alpine:3.20 `
  --docker-volumes /var/run/docker.sock:/var/run/docker.sock `
  --docker-extra-hosts host.docker.internal:host-gateway
```

3. Verifica en GitLab que el runner aparezca en verde.

## 4. Reglas de rama en GitHub

Abre un primer PR (por ejemplo `feature/prueba → dev`) para que GitLab reporte el check. Mira el nombre exacto del check en la pestaña *Checks* del PR (suele ser `ci/gitlab/gitlab.com`) y ejecuta:

```bash
gh auth login
bash scripts/github_protect_branches.sh <usuario>/aegisvolt "ci/gitlab/gitlab.com"
```

Verifica:

```bash
git checkout test && git commit --allow-empty -m x && git push origin test        # rechazado: requiere PR
git push --force origin test                                                       # rechazado: non_fast_forward
```

## 5. Flujo de trabajo (promoción lineal)

```bash
git checkout dev && git pull
git checkout -b feature/tarifa-nocturna
# ... cambios ...
git push -u origin feature/tarifa-nocturna
gh pr create --base dev --head feature/tarifa-nocturna     # pre-merge: tests + terraform plan (workspace dev)
# merge en GitHub → push a dev → post-merge: apply dev + canary en el clúster dev
gh pr create --base test --head dev                        # promoción dev → test
gh pr create --base prod --head test                       # promoción test → prod
```

Un PR `feature/* → test` o `dev → prod` falla en el job `validate-promotion` y no se puede fusionar.

## Alternativa si no consigues GitLab Premium

Usa GitLab como repositorio principal (importa el repo de GitHub y deja GitHub como *push mirror*), protege las ramas `dev`, `test` y `prod` en GitLab (Settings → Repository → Protected branches: *Allowed to push: No one*, *Allowed to force push: off*) y activa *Pipelines must succeed* en los MR. El `.gitlab-ci.yml` ya soporta `merge_request_event` sin cambios.
