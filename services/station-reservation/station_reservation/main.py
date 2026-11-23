"""station-reservation-service: disponibilidad y bloqueo temporal de bahías de carga.

Tabla: aegisvolt-reservations-<env>  (pk, sk)
  CONNECTOR#<connector_id> / META   -> estado del conector (AVAILABLE | RESERVED | CHARGING)
  RESERVATION#<saga_id>    / META   -> reserva (RESERVED | CONFIRMED | RELEASED | VOIDED)

Paso 1 de la SAGA (reservar) y su compensación (liberar). Las dos operaciones
son idempotentes por saga_id y usan TransactWriteItems con condiciones: el
conector y la reserva cambian juntos o no cambia ninguno.
"""

from __future__ import annotations

import logging

from botocore.exceptions import ClientError
from fastapi import FastAPI
from pydantic import BaseModel, Field

from aegis_common import aws, now_iso, to_jsonable
from aegis_common.config import Settings
from aegis_common.flags import FeatureFlags
from aegis_common.logs import configure_logging
from aegis_common.web import BusinessError, install_common

settings = Settings(service_name="station-reservation-service")
log = configure_logging(settings)
flags = FeatureFlags(settings)
app = FastAPI(title="AegisVolt · station-reservation-service", version=settings.app_version)
install_common(app, settings)

ULTRA_CHARGE_KW = 150


def table():
    return aws.table(settings, "reservations")


def connector_key(connector_id: str) -> dict:
    return {"pk": f"CONNECTOR#{connector_id}", "sk": "META"}


def reservation_key(saga_id: str) -> dict:
    return {"pk": f"RESERVATION#{saga_id}", "sk": "META"}


class ReservationRequest(BaseModel):
    saga_id: str = Field(min_length=8)
    connector_id: str
    fleet_id: str
    fast_charge_boost: bool = False


def _get(key: dict) -> dict | None:
    return table().get_item(Key=key, ConsistentRead=True).get("Item")


def _cancelled_reasons(exc: ClientError) -> list[str]:
    return [r.get("Code", "") for r in exc.response.get("CancellationReasons", [])]


@app.get("/stations")
def list_stations():
    items = table().scan().get("Items", [])
    stations: dict[str, dict] = {}
    for item in items:
        if not item["pk"].startswith("CONNECTOR#"):
            continue
        station = stations.setdefault(
            item["station_id"],
            {"station_id": item["station_id"], "name": item.get("station_name"), "city": item.get("city"), "connectors": []},
        )
        station["connectors"].append(
            {
                "connector_id": item["connector_id"],
                "power_kw": item.get("power_kw"),
                "connector_type": item.get("connector_type"),
                "status": item["status"],
                "saga_id": item.get("saga_id"),
                "updated_at": item.get("updated_at"),
            }
        )
    for station in stations.values():
        station["connectors"].sort(key=lambda c: c["connector_id"])
    return to_jsonable(sorted(stations.values(), key=lambda s: s["station_id"]))


@app.get("/reservations/{saga_id}")
def get_reservation(saga_id: str):
    item = _get(reservation_key(saga_id))
    if not item:
        raise BusinessError(404, "RESERVATION_NOT_FOUND", "No existe la reserva")
    return to_jsonable(item)


@app.post("/reservations", status_code=201)
def reserve(req: ReservationRequest):
    """Paso 1 · AVAILABLE -> RESERVED. Reintentar con el mismo saga_id devuelve la misma reserva."""
    existing = _get(reservation_key(req.saga_id))
    if existing:
        if existing["status"] in ("RELEASED", "VOIDED"):
            raise BusinessError(409, "RESERVATION_ALREADY_COMPENSATED", "La reserva de esta SAGA ya fue liberada", reservation=to_jsonable(existing))
        if existing["connector_id"] != req.connector_id:
            raise BusinessError(409, "IDEMPOTENCY_CONFLICT", "El saga_id ya reservó otro conector")
        log.info("reservation_idempotent_replay", extra={"connector_id": req.connector_id})
        return to_jsonable({**existing, "idempotent": True})

    connector = _get(connector_key(req.connector_id))
    if not connector:
        raise BusinessError(404, "CONNECTOR_NOT_FOUND", f"No existe el conector {req.connector_id}")

    priority = "NORMAL"
    if req.fast_charge_boost:
        if not flags.is_enabled("ENABLE_FAST_CHARGE_BOOST"):
            raise BusinessError(422, "FEATURE_DISABLED", "ENABLE_FAST_CHARGE_BOOST está desactivada en este ambiente")
        if int(connector.get("power_kw", 0)) < ULTRA_CHARGE_KW:
            raise BusinessError(422, "NOT_ULTRA_CHARGER", f"El boost requiere un conector de al menos {ULTRA_CHARGE_KW} kW")
        priority = "HIGH"

    now = now_iso()
    reservation = {
        **reservation_key(req.saga_id),
        "saga_id": req.saga_id,
        "connector_id": req.connector_id,
        "station_id": connector["station_id"],
        "fleet_id": req.fleet_id,
        "power_kw": connector.get("power_kw"),
        "priority": priority,
        "status": "RESERVED",
        "created_at": now,
        "updated_at": now,
    }
    try:
        aws.dynamodb(settings).meta.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": settings.table_name("reservations"),
                        "Item": reservation,
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Update": {
                        "TableName": settings.table_name("reservations"),
                        "Key": connector_key(req.connector_id),
                        "UpdateExpression": "SET #s = :reserved, saga_id = :saga, updated_at = :now",
                        "ConditionExpression": "#s = :available",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":reserved": "RESERVED", ":available": "AVAILABLE", ":saga": req.saga_id, ":now": now},
                    }
                },
            ]
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = _cancelled_reasons(exc)
        if reasons and reasons[0] == "ConditionalCheckFailed":
            # Carrera: otra petición con el mismo saga_id ganó. Es la misma operación -> idempotente.
            return to_jsonable({**_get(reservation_key(req.saga_id)), "idempotent": True})
        raise BusinessError(409, "CONNECTOR_UNAVAILABLE", f"El conector {req.connector_id} no está disponible", current_status=connector.get("status")) from exc

    log.info("connector_reserved", extra={"connector_id": req.connector_id, "fleet_id": req.fleet_id, "priority": priority})
    return to_jsonable(reservation)


@app.post("/reservations/{saga_id}/release")
def release(saga_id: str, reason: str = "SAGA_COMPENSATION"):
    """Compensación del paso 1 · RESERVED/CONFIRMED -> RELEASED y conector -> AVAILABLE.

    Idempotente: si ya está liberada no hace nada. Si la reserva nunca llegó a
    existir (p. ej. el paso 1 hizo timeout), deja una 'lápida' VOIDED para que
    una petición de reserva retrasada que llegue después no bloquee el conector.
    """
    existing = _get(reservation_key(saga_id))
    now = now_iso()
    if existing is None:
        try:
            table().put_item(
                Item={**reservation_key(saga_id), "saga_id": saga_id, "status": "VOIDED", "reason": reason, "created_at": now, "updated_at": now, "connector_id": "-"},
                ConditionExpression="attribute_not_exists(pk)",
            )
            log.info("reservation_voided", extra={"reason": reason})
            return {"saga_id": saga_id, "status": "VOIDED", "idempotent": False}
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            existing = _get(reservation_key(saga_id))

    if existing["status"] in ("RELEASED", "VOIDED"):
        log.info("reservation_release_idempotent_replay")
        return to_jsonable({**existing, "idempotent": True})

    try:
        aws.dynamodb(settings).meta.client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": settings.table_name("reservations"),
                        "Key": reservation_key(saga_id),
                        "UpdateExpression": "SET #s = :released, released_at = :now, updated_at = :now, release_reason = :reason",
                        "ConditionExpression": "#s IN (:reserved, :confirmed)",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":released": "RELEASED", ":reserved": "RESERVED", ":confirmed": "CONFIRMED", ":now": now, ":reason": reason},
                    }
                },
                {
                    "Update": {
                        "TableName": settings.table_name("reservations"),
                        "Key": connector_key(existing["connector_id"]),
                        "UpdateExpression": "SET #s = :available, updated_at = :now REMOVE saga_id",
                        "ConditionExpression": "saga_id = :saga",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":available": "AVAILABLE", ":saga": saga_id, ":now": now},
                    }
                },
            ]
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        current = _get(reservation_key(saga_id))
        if current and current["status"] in ("RELEASED", "VOIDED"):
            return to_jsonable({**current, "idempotent": True})
        raise BusinessError(409, "RELEASE_CONFLICT", "No se pudo liberar la reserva", reasons=_cancelled_reasons(exc)) from exc

    log.info("connector_released", extra={"connector_id": existing["connector_id"], "reason": reason})
    return {"saga_id": saga_id, "status": "RELEASED", "connector_id": existing["connector_id"], "idempotent": False}


@app.post("/reservations/{saga_id}/confirm")
def confirm(saga_id: str):
    """Tras activar el cargador: RESERVED -> CONFIRMED y conector -> CHARGING (idempotente)."""
    existing = _get(reservation_key(saga_id))
    if not existing:
        raise BusinessError(404, "RESERVATION_NOT_FOUND", "No existe la reserva")
    if existing["status"] == "CONFIRMED":
        return to_jsonable({**existing, "idempotent": True})
    if existing["status"] != "RESERVED":
        raise BusinessError(409, "INVALID_STATE", f"La reserva está en {existing['status']}")
    now = now_iso()
    aws.dynamodb(settings).meta.client.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": settings.table_name("reservations"),
                    "Key": reservation_key(saga_id),
                    "UpdateExpression": "SET #s = :confirmed, updated_at = :now",
                    "ConditionExpression": "#s = :reserved",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":confirmed": "CONFIRMED", ":reserved": "RESERVED", ":now": now},
                }
            },
            {
                "Update": {
                    "TableName": settings.table_name("reservations"),
                    "Key": connector_key(existing["connector_id"]),
                    "UpdateExpression": "SET #s = :charging, updated_at = :now",
                    "ConditionExpression": "saga_id = :saga",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":charging": "CHARGING", ":saga": saga_id, ":now": now},
                }
            },
        ]
    )
    log.info("reservation_confirmed", extra={"connector_id": existing["connector_id"]})
    return {"saga_id": saga_id, "status": "CONFIRMED", "connector_id": existing["connector_id"], "idempotent": False}


@app.post("/connectors/{connector_id}/finish")
def finish_charging(connector_id: str):
    """Fin de una sesión de carga exitosa: CHARGING -> AVAILABLE (para la demo)."""
    connector = _get(connector_key(connector_id))
    if not connector:
        raise BusinessError(404, "CONNECTOR_NOT_FOUND", "No existe el conector")
    if connector["status"] != "CHARGING":
        return to_jsonable({**connector, "idempotent": True})
    table().update_item(
        Key=connector_key(connector_id),
        UpdateExpression="SET #s = :available, updated_at = :now REMOVE saga_id",
        ConditionExpression="#s = :charging",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":available": "AVAILABLE", ":charging": "CHARGING", ":now": now_iso()},
    )
    return {"connector_id": connector_id, "status": "AVAILABLE"}


logging.getLogger(__name__).debug("station-reservation-service cargado")
