#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Canary Deployment de un microservicio en el clúster EKS del ambiente.
#
#   scripts/canary_deploy.sh <env> <service> <image> <version>
#
# 1. Primer despliegue (no existe <service>-stable): se despliega directo.
# 2. Si ya existe: se crea <service>-canary con la imagen nueva (1 réplica)
#    junto a las 4 réplicas stable -> el Service reparte ~20 % al canary.
# 3. Análisis durante N rondas. El canary PASA solo si:
#      a) 0 fallos de /health en las peticiones que atendió el canary,
#      b) el canary recibió entre MIN y MAX % del tráfico (el split es real),
#      c) 0 reinicios de los pods canary,
#      d) errores del track canary en CloudWatch Logs <= CANARY_MAX_ERRORS
#         (filtro { $.level = "ERROR" && $.track = "canary" } en /aegisvolt/<env>/<service>),
#      e) la alarma <service>-canary-errors de CloudWatch NO está en ALARM.
# 4. PASA -> se promueve: el Deployment stable pasa a la imagen nueva (rolling
#    update al 100 %) y se elimina el canary.
#    FALLA -> rollback automático: se elimina el canary; stable nunca cambió.
#
# Requiere: kubectl (contexto del clúster del ambiente), aws CLI, envsubst.
# -----------------------------------------------------------------------------
set -euo pipefail

ENVIRONMENT="${1:?ambiente}"
SERVICE="${2:?servicio}"
IMAGE="${3:?imagen}"
VERSION="${4:?versión}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=aegisvolt

set -a
# shellcheck disable=SC1090
source "$ROOT/k8s/env/${ENVIRONMENT}.env"
set +a
SIMULATE_BAD_RELEASE="${SIMULATE_BAD_RELEASE:-false}"
LOG_GROUP="/aegisvolt/${ENVIRONMENT}/${SERVICE}"
ALARM="aegisvolt-${ENVIRONMENT}-${SERVICE}-canary-errors"

log() { printf '\033[1;36m[canary %s/%s]\033[0m %s\n' "$ENVIRONMENT" "$SERVICE" "$*"; }

render() { # TRACK REPLICAS IMAGE VERSION BAD
  TRACK="$1" REPLICAS="$2" IMAGE="$3" VERSION="$4" SIMULATE_BAD_RELEASE="$5" SERVICE="$SERVICE" \
    envsubst '${SERVICE} ${TRACK} ${REPLICAS} ${IMAGE} ${VERSION} ${SIMULATE_BAD_RELEASE}' < "$ROOT/k8s/templates/deployment.yaml"
}

wait_rollout() { kubectl -n "$NS" rollout status "deploy/$1" --timeout="${ROLLOUT_TIMEOUT:-180s}"; }

if ! kubectl -n "$NS" get deploy "${SERVICE}-stable" >/dev/null 2>&1; then
  log "Primer despliegue: ${SERVICE}-stable x${STABLE_REPLICAS} con ${IMAGE}"
  render stable "$STABLE_REPLICAS" "$IMAGE" "$VERSION" false | kubectl apply -f -
  wait_rollout "${SERVICE}-stable"
  exit 0
fi

CURRENT_IMAGE="$(kubectl -n "$NS" get deploy "${SERVICE}-stable" -o jsonpath='{.spec.template.spec.containers[0].image}')"
if [[ "$CURRENT_IMAGE" == "$IMAGE" ]]; then
  log "La versión stable ya es ${IMAGE}; nada que hacer."
  exit 0
fi

# Se asegura que stable tenga sus réplicas completas antes de abrir el canary.
kubectl -n "$NS" scale "deploy/${SERVICE}-stable" --replicas="$STABLE_REPLICAS" >/dev/null
wait_rollout "${SERVICE}-stable"

START_MS=$(( $(date +%s) * 1000 ))
log "Desplegando canary ${IMAGE} (${CANARY_REPLICAS} réplica vs ${STABLE_REPLICAS} stable)"
render canary "$CANARY_REPLICAS" "$IMAGE" "$VERSION" "$SIMULATE_BAD_RELEASE" | kubectl apply -f -

rollback() {
  log "❌ ROLLBACK: $1"
  kubectl -n "$NS" delete deploy "${SERVICE}-canary" --ignore-not-found
  log "Tráfico 100 % de vuelta en ${CURRENT_IMAGE}"
  exit 1
}

if ! wait_rollout "${SERVICE}-canary"; then
  rollback "el canary no llegó a Ready"
fi

# Sonda desde DENTRO del clúster (un pod stable del gateway, que trae Python):
# pega N veces al Service y cuenta qué track respondió cada vez.
PROBE_POD_DEPLOY="graphql-gateway-stable"
kubectl -n "$NS" get deploy "$PROBE_POD_DEPLOY" >/dev/null 2>&1 || PROBE_POD_DEPLOY="${SERVICE}-stable"
PROBE=$(cat <<PY
import json, urllib.request, urllib.error
url = "http://${SERVICE}.${NS}.svc.cluster.local/health"
res = {"stable_ok": 0, "stable_fail": 0, "canary_ok": 0, "canary_fail": 0}
for _ in range(${CANARY_PROBES_PER_ROUND}):
    try:
        r = urllib.request.urlopen(url, timeout=3); code = r.status; track = r.headers.get("X-AegisVolt-Track", "?")
    except urllib.error.HTTPError as e:
        code = e.code; track = e.headers.get("X-AegisVolt-Track", "?")
    except Exception:
        code = 0; track = "?"
    key = ("canary" if track == "canary" else "stable") + ("_ok" if code == 200 else "_fail")
    res[key] += 1
print(json.dumps(res))
PY
)

for round in $(seq 1 "$CANARY_ANALYSIS_ROUNDS"); do
  sleep "$CANARY_ANALYSIS_INTERVAL"
  RESULT="$(kubectl -n "$NS" exec "deploy/${PROBE_POD_DEPLOY}" -- python -c "$PROBE")"
  CANARY_OK=$(echo "$RESULT" | jq '.canary_ok'); CANARY_FAIL=$(echo "$RESULT" | jq '.canary_fail')
  TOTAL=$(echo "$RESULT" | jq '[.[]] | add')
  CANARY_PCT=$(( (CANARY_OK + CANARY_FAIL) * 100 / (TOTAL > 0 ? TOTAL : 1) ))
  RESTARTS=$(kubectl -n "$NS" get pods -l "app=${SERVICE},track=canary" -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' | awk '{s+=$1} END {print s+0}')
  CW_ERRORS=$(aws logs filter-log-events --log-group-name "$LOG_GROUP" --start-time "$START_MS" \
      --filter-pattern '{ ($.level = "ERROR") && ($.track = "canary") }' --query 'length(events)' --output text 2>/dev/null || echo 0)
  [[ "$CW_ERRORS" =~ ^[0-9]+$ ]] || CW_ERRORS=0
  ALARM_STATE=$(aws cloudwatch describe-alarms --alarm-names "$ALARM" --query 'MetricAlarms[0].StateValue' --output text 2>/dev/null || echo "UNKNOWN")

  log "ronda ${round}/${CANARY_ANALYSIS_ROUNDS}: sonda=${RESULT} canary=${CANARY_PCT}% reinicios=${RESTARTS} errores_cloudwatch=${CW_ERRORS} alarma=${ALARM_STATE}"

  (( CANARY_FAIL == 0 )) || rollback "${CANARY_FAIL} fallos de /health en el canary"
  (( RESTARTS == 0 )) || rollback "${RESTARTS} reinicios de pods canary"
  (( CW_ERRORS <= CANARY_MAX_ERRORS )) || rollback "${CW_ERRORS} errores del canary en CloudWatch (${LOG_GROUP})"
  [[ "$ALARM_STATE" != "ALARM" ]] || rollback "alarma ${ALARM} en estado ALARM"
  if (( CANARY_PCT < CANARY_MIN_TRAFFIC_PCT || CANARY_PCT > CANARY_MAX_TRAFFIC_PCT )); then
    rollback "el canary recibió ${CANARY_PCT}% del tráfico (esperado ${CANARY_MIN_TRAFFIC_PCT}-${CANARY_MAX_TRAFFIC_PCT}%)"
  fi
done

log "✅ Análisis superado: promoviendo ${IMAGE} al 100 %"
render stable "$STABLE_REPLICAS" "$IMAGE" "$VERSION" false | kubectl apply -f -
wait_rollout "${SERVICE}-stable"
kubectl -n "$NS" delete deploy "${SERVICE}-canary" --ignore-not-found
log "Promoción completa"
