"""energy-dispatch-service: orden de activación física del cargador y registro de telemetría.

Tabla: aegisvolt-dispatch-<env>  (pk, sk)
  SESSION#<saga_id>         / META          -> sesión (ACTIVE | FAILED | COMPLETED)
  TELEMETRY#<connector_id>  / <timestamp>   -> lecturas de potencia/energía/voltaje

Paso 3 de la SAGA. Si el cargador "reporta" un fallo físico (flag
SIMULATE_CHARGER_HARDWARE_FAULT activa en el ambiente) responde 502 y el
orquestador compensa: reembolsa en billing y luego libera la bahía.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

from botocore.exceptions import ClientError
from fastapi import FastAPI
from pydantic import BaseModel, Field

from aegis_common import aws, now_iso, to_jsonable
from aegis_common.config import Settings
from aegis_common.flags import FeatureFlags
from aegis_common.logs import configure_logging
from aegis_common.web import BusinessError, install_common

settings = Settings(service_name="energy-dispatch-service")
log = configure_logging(settings)
flags = FeatureFlags(settings)
app = FastAPI(title="AegisVolt · energy-dispatch-service", version=settings.app_version)
install_common(app, settings)


def table():
    return aws.table(settings, "dispatch")


def session_key(saga_id: str) -> dict:
    return {"pk": f"SESSION#{saga_id}", "sk": "META"}


class SessionRequest(BaseModel):
    saga_id: str = Field(min_length=8)
    connector_id: str
    energy_kwh: Decimal = Field(gt=0, le=500)
    power_kw: int = Field(ge=1, le=1000)


def _get(key: dict) -> dict | None:
    return table().get_item(Key=key, ConsistentRead=True).get("Item")


def _record_telemetry(connector_id: str, saga_id: str, power_kw: int, delivered_kwh: Decimal) -> dict:
    ts = now_iso()
    reading = {
        "pk": f"TELEMETRY#{connector_id}",
        "sk": ts,
        "saga_id": saga_id,
        "connector_id": connector_id,
        "power_kw": Decimal(str(round(power_kw * random.uniform(0.82, 0.98), 2))),
        "voltage_v": Decimal(str(round(random.uniform(395, 410), 1))),
        "temperature_c": Decimal(str(round(random.uniform(28, 41), 1))),
        "energy_delivered_kwh": delivered_kwh,
        "recorded_at": ts,
    }
    table().put_item(Item=reading)
    return reading


@app.post("/sessions", status_code=201)
def start_session(req: SessionRequest):
    """Paso 3 · intenta iniciar el flujo eléctrico. Idempotente por saga_id."""
    existing = _get(session_key(req.saga_id))
    if existing:
        if existing["status"] == "FAILED":
            raise BusinessError(502, existing.get("error_code", "CHARGER_HARDWARE_FAULT"), "El cargador ya reportó fallo para esta SAGA", idempotent=True)
        log.info("session_idempotent_replay")
        return to_jsonable({**existing, "idempotent": True})

    now = now_iso()
    fault = flags.is_enabled("SIMULATE_CHARGER_HARDWARE_FAULT")
    session = {
        **session_key(req.saga_id),
        "saga_id": req.saga_id,
        "connector_id": req.connector_id,
        "requested_kwh": req.energy_kwh,
        "power_kw": req.power_kw,
        "status": "FAILED" if fault else "ACTIVE",
        "started_at": now,
        "updated_at": now,
    }
    if fault:
        session.update({"error_code": "CHARGER_HARDWARE_FAULT", "error_detail": "Falla de aislamiento en el módulo de potencia (simulada por feature flag)"})
    try:
        table().put_item(Item=session, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return start_session(req)  # carrera con el mismo saga_id: se responde como réplica

    if fault:
        log.error("charger_hardware_fault", extra={"connector_id": req.connector_id, "error_code": "CHARGER_HARDWARE_FAULT"})
        raise BusinessError(502, "CHARGER_HARDWARE_FAULT", "El cargador reportó un error físico al iniciar la transferencia de energía")

    _record_telemetry(req.connector_id, req.saga_id, req.power_kw, Decimal("0"))
    log.info("charging_session_started", extra={"connector_id": req.connector_id, "power_kw": req.power_kw})
    return to_jsonable({**session, "idempotent": False})


@app.get("/sessions/{saga_id}")
def get_session(saga_id: str):
    item = _get(session_key(saga_id))
    if not item:
        raise BusinessError(404, "SESSION_NOT_FOUND", "No existe la sesión")
    return to_jsonable(item)


@app.get("/telemetry/{connector_id}")
def telemetry(connector_id: str, limit: int = 20):
    """Últimas lecturas. Si hay una sesión activa se genera una lectura nueva (simulación del cargador)."""
    items = table().query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": f"TELEMETRY#{connector_id}"},
        ScanIndexForward=False,
        Limit=limit,
    ).get("Items", [])
    if items:
        last = items[0]
        session = _get(session_key(last["saga_id"]))
        if session and session["status"] == "ACTIVE":
            started = datetime.fromisoformat(session["started_at"])
            hours = Decimal(str((datetime.now(UTC) - started).total_seconds() / 3600))
            delivered = min(Decimal(session["requested_kwh"]), (hours * Decimal(session["power_kw"])).quantize(Decimal("0.01")))
            items.insert(0, _record_telemetry(connector_id, last["saga_id"], int(session["power_kw"]), delivered))
    return to_jsonable(items[:limit])
