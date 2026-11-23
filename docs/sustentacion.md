# Guía de sustentación

Respuestas cortas y comandos para cada eje del banco de preguntas. Todos los comandos asumen
PowerShell en la raíz del repo con:

```powershell
$env:AWS_ENDPOINT_URL="http://localhost:4566"; $env:AWS_ACCESS_KEY_ID="test"; $env:AWS_SECRET_ACCESS_KEY="test"; $env:AWS_DEFAULT_REGION="us-east-1"
```

---

## 1. Infraestructura como Código (30 %)

### ¿Cómo garantiza que un `terraform apply` en test no sobrescriba las tablas ni el estado de prod?

Hay cuatro capas de protección (están en `infra/main.tf`):

1. **Estado aislado por workspace.** El backend S3 usa `workspace_key_prefix = "env:"`, así que cada workspace tiene su propio objeto: `env:/test/aegisvolt/terraform.tfstate` y `env:/prod/aegisvolt/terraform.tfstate`. Con el workspace `test` seleccionado, Terraform solo lee y escribe el estado de test. Los recursos de prod no existen en ese estado, así que no puede modificarlos ni destruirlos.
2. **Nombres parametrizados con `${terraform.workspace}`.** Todo recurso lleva el ambiente en el nombre (`aegisvolt-billing-test` frente a `aegisvolt-billing-prod`, `/aegisvolt/test/...`, `aegisvolt-test-eks`), así que no hay colisiones.
3. **Guardas** (`terraform_data.workspace_guard`). El plan falla si el workspace no es dev, test o prod (por ejemplo `default`) o si el `.tfvars` no corresponde al workspace (`var.environment != terraform.workspace`).
4. **Protección de borrado.** Las tablas de prod tienen `deletion_protection_enabled = true`. En CI, cada ambiente tiene su propio `resource_group`, así que nunca corren dos apply sobre el mismo ambiente. Además, la tabla de locks evita que dos procesos escriban el mismo estado a la vez.

```powershell
cd infra
terraform workspace list
terraform workspace select test; terraform state list | Select-String dynamodb   # solo tablas *-test
aws s3 ls s3://aegisvolt-tfstate --recursive                                       # env:/dev, env:/test, env:/prod
terraform workspace select test; terraform plan "-var-file=env/prod.tfvars"          # FALLA por la guarda
```

### Muestre las versiones anteriores del tfstate de dev y cómo restauraría una

```powershell
python scripts/aegis.py tfstate --env dev                          # lista versiones (Versioning: Enabled)
python scripts/aegis.py tfstate --env dev --download <version_id>  # inspeccionar la versión antigua (serial)
python scripts/aegis.py tfstate --env dev --restore <version_id>   # restaurar
cd infra; terraform workspace select dev; terraform plan "-var-file=env/dev.tfvars"   # verificar
```

Para restaurar se copia la versión antigua encima de la actual (`copy-object` con `versionId`). No se borra nada: la versión corrupta queda en el historial como evidencia y la restaurada pasa a ser la *latest*. El bucket conserva las versiones no actuales durante 90 días (lifecycle) y tiene cifrado y bloqueo de acceso público (`infra/bootstrap/main.tf`). Equivalente con la AWS CLI: `aws s3api list-object-versions --bucket aegisvolt-tfstate --prefix env:/dev/`.

### Recorrido de un paquete desde API Gateway hasta el pod

```
Cliente dentro de la VPC
  │ resuelve <api-id>.execute-api.us-east-1.amazonaws.com → DNS privado del Interface VPCE (private_dns_enabled)
  ▼
ENI del VPCE execute-api en la subred privada  ── Security Group vpce-sg: 443 solo desde el CIDR de la VPC
  ▼
API Gateway (endpoint PRIVATE) ── resource policy: Deny si aws:SourceVpce ≠ el VPCE de este ambiente
  ▼  integración HTTP_PROXY con connection_type = VPC_LINK
VPC Link → NLB interno (internal = true, subredes privadas) listener TCP 80
  ▼  target group → NodePort 30080 de los nodos EKS (registro automático vía ASG del node group)
Nodo EKS (Security Group eks-sg: 30080 solo desde el CIDR de la VPC)
  ▼  kube-proxy (iptables) → Service graphql-gateway → pod (stable o canary)
Pod → Floci en 10.<x>.0.2:4566 (IP de Floci dentro de la red de la VPC del ambiente; k8s/env/<env>.env)
Pod → DynamoDB/S3: tabla de rutas privada → ruta a la prefix list del Gateway Endpoint (no hay 0.0.0.0/0)
Pod → CloudWatch Logs/ECR/STS: Interface Endpoints (ENIs privadas)
```

> **Sobre el emulador:** Floci no implementa dos APIs de este recorrido: `CreateVpcEndpointServiceConfiguration` (PrivateLink propio) y el ASG real del node group (registrar los nodos en el target group). Terraform los declara igual y solo los omite cuando `aws_endpoint` apunta a Floci (`local.on_emulator` en `infra/main.tf`): con AWS real (`aws_endpoint = null`) se crean. En la demo local el último salto se hace con `kubectl port-forward`.

La tabla de rutas privada **no tiene ruta a internet** (ni NAT ni IGW): todo lo que sale hacia AWS pasa por un VPC Endpoint. Además, `modules/k8s_ingress` publica el NLB como **VPC Endpoint Service (PrivateLink)** y crea un Interface VPCE que lo consume: ese es el VPCE "hacia los servicios del clúster".

```powershell
cd infra; terraform output vpc_endpoints; terraform output k8s_private_link; terraform output api_gateway
aws ec2 describe-route-tables --filters Name=tag:Name,Values=aegisvolt-dev-rt-private
```

---

## 2. CI/CD, Git y Canary (25 %)

### Intente un `git push --force` o un push directo a test

```bash
git checkout test && git commit --allow-empty -m "push directo" && git push origin test
# remote: error: GH013: Repository rule violations found for refs/heads/test.
#   - Changes must be made through a pull request.
git push --force origin test
#   - Cannot force-push to this branch
```

La regla es el Ruleset `aegisvolt-protected-environments` (GitHub → Settings → Rules → Rulesets), creado con `scripts/github_protect_branches.sh`. Contiene `non_fast_forward`, `deletion`, `pull_request` y `required_status_checks` (`gitlab-pipeline`), y no tiene bypass para nadie. El check `gitlab-pipeline` es el workflow de GitHub Actions que lanza el pipeline en GitLab Free y espera su resultado.

La cadena de promoción se hace cumplir con el job `validate-promotion` (`scripts/ci/validate_promotion.sh`): un PR `feature/x → test` queda en rojo y el Merge no se habilita.

### ¿Cómo determina el YAML sobre qué ambiente correr `terraform apply`?

`scripts/ci/detect_env.sh`, invocado desde `.terraform` en `.gitlab-ci.yml`:

- **Pre-merge:** GitHub Actions lanza el pipeline con `AEGIS_PIPELINE_MODE=pre_merge` y `PR_TARGET_BRANCH`; el script toma la rama **destino** del PR y corre `terraform plan` en ese workspace.
- **Post-merge:** el merge produce un push a `dev`, `test` o `prod`; Actions lanza el pipeline con `AEGIS_PIPELINE_MODE=post_merge` sobre esa rama. El job `terraform-apply` (regla `.post_merge`) toma `CI_COMMIT_BRANCH`, lo traduce con un `case` a `TF_ENV` y ejecuta `terraform workspace select -or-create $TF_ENV` y `terraform apply -auto-approve -var-file=env/$TF_ENV.tfvars`. Una rama que no sea de ambiente hace fallar el job.

### ¿Qué evalúa el Canary antes de pasar del 20 % al 100 %?

`scripts/canary_deploy.sh` despliega `<servicio>-canary` con 1 réplica junto a 4 réplicas stable. El Service selecciona solo por `app`, así que el canary recibe cerca del 20 % del tráfico. Durante 3 rondas de 20 s, el canary pasa solo si cumple todo esto:

| Condición | Fuente |
|---|---|
| 0 fallos de `/health` en las respuestas del canary | sonda de 40 peticiones desde dentro del clúster (header `X-AegisVolt-Track`) |
| El canary recibió entre el 5 % y el 40 % del tráfico | la misma sonda: demuestra que el split es real |
| 0 reinicios de los pods canary | `kubectl get pods -l track=canary` |
| 0 errores del canary en CloudWatch | `filter-log-events` con `{ $.level = "ERROR" && $.track = "canary" }` en `/aegisvolt/<env>/<svc>` |
| La alarma `aegisvolt-<env>-<svc>-canary-errors` no está en ALARM | métrica creada por el filtro de Terraform |

Si se cumple, el Deployment stable pasa a la imagen nueva con un rolling update (`maxUnavailable: 0`) y se borra el canary. Si algo falla, se borra el canary, stable nunca cambió y el job queda en rojo (**rollback automático**). Para demostrarlo, lanza el pipeline con la variable `SIMULATE_BAD_RELEASE=true`: el canary devuelve 500 en `/health` y se revierte.

---

## 3. SAGA y Feature Flags (25 %)

### Active la flag de fallo en test, dispare la mutación y muestre la compensación en DynamoDB

```powershell
python scripts/aegis.py flags --env test --set SIMULATE_CHARGER_HARDWARE_FAULT=true   # o el switch del frontend
```

Luego, en el frontend (ambiente test), reserva una bahía. También se puede hacer en GraphQL:

```graphql
mutation {
  reserveAndActivateCharge(input: { fleetId: "FLEET-RAPPI-EV", connectorId: "BOG80-U1", energyKwh: 30 }) {
    message saga { sagaId status failureReason compensations { step action result } }
  }
}
```

```powershell
python scripts/aegis.py inspect --env test --saga <saga_id>
#   saga: COMPENSATED · reservation: RELEASED · charge: REFUNDED · session: FAILED
aws dynamodb get-item --table-name aegisvolt-billing-test --key '{\"pk\":{\"S\":\"CHARGE#<saga_id>\"},\"sk\":{\"S\":\"META\"}}'
aws dynamodb query --table-name aegisvolt-billing-test --key-condition-expression "pk = :p" --expression-attribute-values '{\":p\":{\"S\":\"WALLET#FLEET-RAPPI-EV\"}}'   # DEBIT y REFUND en el ledger
```

El orden es inverso al de ejecución: primero `REFUND` (billing) y después `RELEASE` (reservas). Se ve en el timeline del frontend y en `saga.events`.

### ¿Cómo evita problemas de idempotencia si una compensación se procesa dos veces?

1. **Clave de idempotencia = `saga_id`.** Cada operación crea un único item (`CHARGE#<saga_id>`, `RESERVATION#<saga_id>`, `SESSION#<saga_id>`) con `ConditionExpression attribute_not_exists(pk)`. Un reintento encuentra el item y devuelve el mismo resultado (`idempotent: true`) sin repetir el efecto.
2. **Transiciones de estado condicionales y atómicas.** El reembolso usa `TransactWriteItems`: `status CHARGED → REFUNDED` con la condición `status = CHARGED`, más `balance + amount`, más la entrada del ledger. La condición solo se cumple una vez. Si la petición llega dos veces, la segunda falla la condición y se responde como réplica: **el saldo nunca se devuelve dos veces**.
3. **Lápidas (`VOIDED`).** Si se compensa algo que nunca llegó (timeout), se escribe un item `VOIDED`. Si la petición original llega tarde, choca con `attribute_not_exists` y se rechaza: no aparece un cobro ni una reserva "fantasma" después del rollback.
4. **En la entrada.** La mutación recibe `idempotencyKey`. Repetirla devuelve la misma SAGA y otro payload con la misma clave da conflicto. Pruébalo con el botón "Reenviar la misma solicitud".
5. **Compensación con reintentos (3, con backoff).** Si un reembolso no se confirma, la SAGA queda en `COMPENSATION_FAILED` y **no** libera la reserva fuera de orden. `retryCompensation` la reanuda y Prefect la detecta.

Pruebas: `tests/integration` (`test_charge_and_refund_are_applied_exactly_once`, `test_release_is_idempotent`, `test_late_charge_after_void_is_blocked`) y `tests/unit/test_saga_orchestrator.py`.

---

## 4. Observabilidad y presentación (20 %)

### Prefect + rastrear en CloudWatch el correlation_id de una SAGA fallida

```powershell
python observability/prefect/flows.py run    # o el deployment programado en http://127.0.0.1:4200
```

En el dashboard: el flow run `aegisvolt-observability` muestra las tareas por ambiente y los artefactos `aegisvolt-report` (markdown con SAGAs compensadas, huérfanas e inconsistentes) y `aegisvolt-environments` (tabla). Las métricas quedan en `AegisVolt/Observability` y el log en `/aegisvolt/<env>/observability`.

```powershell
python scripts/aegis.py trace --env test --saga <saga_id>
aws logs filter-log-events --log-group-name /aegisvolt/test/graphql-gateway --filter-pattern '{ $.correlation_id = \"<saga_id>\" }'
```

Cada línea es JSON con `correlation_id` (igual al saga_id), `environment`, `service`, `version` y `track`. El gateway propaga `X-Correlation-ID` a los tres servicios, así que el mismo id aparece en los cuatro grupos de logs.

### Cambie una flag en dev sin redesplegar y muestre que prod no cambia

1. Abre el frontend en **dev**. En "Feature flags de dev", activa *Tarifa dinámica* o *Simular falla física*. Esto hace un `setFeatureFlag` sobre `aegisvolt-feature-flags-dev`.
2. Los servicios leen la flag con un caché de 3 s: la próxima reserva ya se comporta distinto (precio con tarifa pico o SAGA revertida). No hubo redespliegue: `kubectl get pods` muestra la misma edad.
3. Cambia a **prod**: la franja se pone roja y la flag sigue en su valor, porque prod lee **su propia tabla** (`aegisvolt-feature-flags-prod`). En prod, `SIMULATE_CHARGER_HARDWARE_FAULT` está bloqueada por código (`PROD_LOCKED_FLAGS`).
4. Terraform no revierte el cambio en el próximo apply (`lifecycle { ignore_changes = [item] }`).
