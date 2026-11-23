"""Flujos de Prefect para la observabilidad de AegisVolt.

Flow  aegisvolt-observability
  ├─ check-infrastructure[env]  tablas DynamoDB y grupos de logs del ambiente
  ├─ check-api-health[env]      GraphQL { environment } del gateway (latencia, servicios)
  ├─ audit-sagas[env]           consistencia SAGA vs tablas (huérfanas, compensadas, inconsistentes)
  ├─ recover-orphans[env]       (opcional) retryCompensation de las huérfanas
  └─ publish[env]               métricas a CloudWatch + artefactos en el dashboard de Prefect

Ejecutar una vez:
    python observability/prefect/flows.py run
Dejarlo programado cada 5 minutos (aparece en Deployments del dashboard):
    python observability/prefect/flows.py serve
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import httpx
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact, create_table_artifact

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import SagaAudit, audit  # noqa: E402

ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
GRAPHQL = {
    "dev": os.getenv("AEGIS_GRAPHQL_DEV", "http://localhost:8081/graphql"),
    "test": os.getenv("AEGIS_GRAPHQL_TEST", "http://localhost:8082/graphql"),
    "prod": os.getenv("AEGIS_GRAPHQL_PROD", "http://localhost:8083/graphql"),
}
TABLES = ("reservations", "billing", "dispatch", "sagas", "feature-flags")


def _aws(service: str):
    return boto3.client(service, endpoint_url=ENDPOINT, region_name=REGION, aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"), aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"))


def _table(env: str, kind: str):
    return boto3.resource("dynamodb", endpoint_url=ENDPOINT, region_name=REGION, aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"), aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test")).Table(f"aegisvolt-{kind}-{env}")


@task(name="check-infrastructure", retries=2, retry_delay_seconds=3)
def check_infrastructure(env: str) -> dict:
    logger = get_run_logger()
    ddb, logs = _aws("dynamodb"), _aws("logs")
    tables = {}
    for kind in TABLES:
        name = f"aegisvolt-{kind}-{env}"
        try:
            tables[name] = ddb.describe_table(TableName=name)["Table"]["TableStatus"]
        except ddb.exceptions.ResourceNotFoundException:
            tables[name] = "MISSING"
    groups = [g["logGroupName"] for g in logs.describe_log_groups(logGroupNamePrefix=f"/aegisvolt/{env}/").get("logGroups", [])]
    ok = all(status == "ACTIVE" for status in tables.values())
    logger.info("[%s] tablas=%s grupos_de_logs=%d", env, tables, len(groups))
    return {"environment": env, "tables": tables, "log_groups": groups, "ok": ok}


@task(name="check-api-health", retries=1, retry_delay_seconds=2)
def check_api_health(env: str) -> dict:
    logger = get_run_logger()
    started = time.perf_counter()
    try:
        response = httpx.post(GRAPHQL[env], json={"query": "{ environment { name version track services } }"}, timeout=8)
        latency = round((time.perf_counter() - started) * 1000, 1)
        data = response.json()["data"]["environment"]
        services_ok = all(v == "ok" for v in data["services"].values())
        up = response.status_code == 200 and data["name"] == env and services_ok
        logger.info("[%s] API %s en %s ms (versión %s, servicios %s)", env, "UP" if up else "DEGRADED", latency, data["version"], data["services"])
        return {"environment": env, "up": up, "latency_ms": latency, "version": data["version"], "services": data["services"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] API no disponible en %s: %s", env, GRAPHQL[env], exc)
        return {"environment": env, "up": False, "latency_ms": None, "error": str(exc)}


@task(name="audit-sagas")
def audit_sagas(env: str, orphan_minutes: int = 5) -> SagaAudit:
    logger = get_run_logger()
    try:
        sagas = _table(env, "sagas").scan().get("Items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] no se pudo leer la tabla de sagas: %s", env, exc)
        return SagaAudit(environment=env)
    reservations, billing, dispatch = _table(env, "reservations"), _table(env, "billing"), _table(env, "dispatch")

    def lookup(saga_id: str):
        return (
            reservations.get_item(Key={"pk": f"RESERVATION#{saga_id}", "sk": "META"}).get("Item"),
            billing.get_item(Key={"pk": f"CHARGE#{saga_id}", "sk": "META"}).get("Item"),
            dispatch.get_item(Key={"pk": f"SESSION#{saga_id}", "sk": "META"}).get("Item"),
        )

    result = audit(env, sagas, lookup, orphan_after=timedelta(minutes=orphan_minutes))
    logger.info("[%s] %d SAGAs %s | huérfanas=%d inconsistentes=%d", env, result.total, result.by_status, len(result.orphans), len(result.inconsistencies))
    for item in result.inconsistencies:
        logger.error("[%s] SAGA inconsistente %s: %s", env, item["saga_id"], item["problems"])
    return result


@task(name="recover-orphans")
def recover_orphans(env: str, result: SagaAudit) -> list[dict]:
    logger = get_run_logger()
    recovered = []
    mutation = "mutation($id: ID!) { retryCompensation(sagaId: $id) { success message saga { status } } }"
    for orphan in result.orphans:
        try:
            data = httpx.post(GRAPHQL[env], json={"query": mutation, "variables": {"id": orphan["saga_id"]}}, timeout=30).json()["data"]["retryCompensation"]
            recovered.append({"saga_id": orphan["saga_id"], "result": data["message"]})
            logger.info("[%s] SAGA huérfana %s -> %s", env, orphan["saga_id"], data["message"])
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] no se pudo recuperar %s: %s", env, orphan["saga_id"], exc)
    return recovered


@task(name="publish")
def publish(env: str, infra: dict, api: dict, result: SagaAudit) -> dict:
    """Métricas a CloudWatch (AegisVolt/Observability) + log JSON en /aegisvolt/<env>/observability."""
    metrics = {
        "ApiUp": 1 if api.get("up") else 0,
        "ApiLatencyMs": api.get("latency_ms") or 0,
        "InfraOk": 1 if infra["ok"] else 0,
        "SagaTotal": result.total,
        "SagaCompleted": result.by_status.get("COMPLETED", 0),
        "SagaCompensated": result.by_status.get("COMPENSATED", 0),
        "SagaCompensationFailed": result.by_status.get("COMPENSATION_FAILED", 0),
        "SagaOrphaned": len(result.orphans),
        "SagaInconsistent": len(result.inconsistencies),
    }
    try:
        _aws("cloudwatch").put_metric_data(
            Namespace="AegisVolt/Observability",
            MetricData=[{"MetricName": k, "Value": float(v), "Dimensions": [{"Name": "Environment", "Value": env}]} for k, v in metrics.items()],
        )
        logs = _aws("logs")
        group, stream = f"/aegisvolt/{env}/observability", f"prefect-{datetime.now(UTC):%Y%m%d}"
        try:
            logs.create_log_stream(logGroupName=group, logStreamName=stream)
        except Exception:  # noqa: BLE001
            pass
        logs.put_log_events(
            logGroupName=group,
            logStreamName=stream,
            logEvents=[{"timestamp": int(time.time() * 1000), "message": json.dumps({"level": "ERROR" if not result.healthy else "INFO", "service": "prefect-observability", "environment": env, "message": "observability_report", **metrics}, default=str)}],
        )
    except Exception as exc:  # noqa: BLE001
        get_run_logger().warning("[%s] no se publicaron métricas en CloudWatch: %s", env, exc)
    return {"environment": env, **metrics}


@flow(name="aegisvolt-observability", log_prints=True)
def aegisvolt_observability(environments: list[str] | None = None, orphan_minutes: int = 5, auto_recover: bool = False) -> list[dict]:
    environments = environments or ["dev", "test", "prod"]
    rows, details = [], []
    for env in environments:
        infra = check_infrastructure(env)
        api = check_api_health(env)
        result = audit_sagas(env, orphan_minutes)
        recovered = recover_orphans(env, result) if auto_recover and result.orphans else []
        rows.append(publish(env, infra, api, result))
        details.append((env, infra, api, result, recovered))

    create_table_artifact(key="aegisvolt-environments", table=rows, description="Salud y SAGAs por ambiente")
    md = ["# AegisVolt · reporte de observabilidad", f"_Generado {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC_", ""]
    for env, infra, api, result, recovered in details:
        icon = "🟢" if api.get("up") and infra["ok"] and result.healthy else "🔴"
        md += [
            f"## {icon} {env}",
            f"- API: {'UP' if api.get('up') else 'DOWN'} · latencia {api.get('latency_ms')} ms · versión {api.get('version', '-')}",
            f"- Tablas: {', '.join(f'{k}={v}' for k, v in infra['tables'].items())}",
            f"- SAGAs: {result.total} · {result.by_status}",
            f"- Compensadas: {len(result.compensated)} · Huérfanas: {len(result.orphans)} · Inconsistentes: {len(result.inconsistencies)}",
        ]
        md += [f"  - ⚠️ `{o['saga_id']}` atascada en {o['status']} hace {o['minutes_stuck']} min" for o in result.orphans]
        md += [f"  - ❌ `{i['saga_id']}`: {'; '.join(i['problems'])}" for i in result.inconsistencies]
        md += [f"  - ♻️ `{r['saga_id']}` -> {r['result']}" for r in recovered]
        md += [f"  - ↩️ `{c['saga_id']}` compensada por {c['failure_reason']}" for c in result.compensated[:10]]
        md.append("")
    create_markdown_artifact(key="aegisvolt-report", markdown="\n".join(md), description="Reporte de SAGAs y salud")
    return rows


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        aegisvolt_observability.serve(name="aegisvolt-observability-cada-5-min", interval=300, parameters={"auto_recover": False})
    else:
        print(json.dumps(aegisvolt_observability(), indent=2, default=str))
