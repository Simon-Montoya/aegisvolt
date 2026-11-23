"""Orquestador de la SAGA "Reservar y Activar Carga".

    Paso 1  station-reservation-service  reservar conector     | compensación: liberar
    Paso 2  billing-wallet-service       cobrar saldo estimado | compensación: reembolsar
    Paso 3  energy-dispatch-service      activar el cargador   | (no requiere compensación)

Reglas:
  * Falla el paso 3  -> reembolsar (2) y luego liberar (1): orden inverso.
  * Falla el paso 2 por fondos -> liberar (1).
  * Falla el paso 1 por negocio -> no hay nada que compensar (REJECTED).
  * Fallo técnico (timeout / 5xx sin código) = resultado INCIERTO: el paso
    también se compensa. Es seguro porque las compensaciones son idempotentes y,
    si la operación original nunca llegó, dejan una lápida VOIDED que bloquea
    una petición retrasada.
  * El estado se persiste ANTES y DESPUÉS de cada paso (write-ahead) en la tabla
    aegisvolt-sagas-<env>. Una SAGA que quede a mitad de camino es "huérfana" y
    la detecta el flujo de Prefect; se puede reanudar con retry_compensation().
  * Idempotencia de entrada: el cliente envía un idempotencyKey (UUID) que es el
    saga_id. Repetir la mutación devuelve la misma SAGA, no crea otra.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from botocore.exceptions import ClientError

from aegis_common import now_iso, to_jsonable
from aegis_common.logs import correlation_id_var

from .clients import BusinessFailure, ServiceClients, TechnicalFailure

log = logging.getLogger("saga")

TERMINAL = {"COMPLETED", "REJECTED", "COMPENSATED", "COMPENSATION_FAILED"}
COMPENSATION_ATTEMPTS = 3


class IdempotencyConflict(Exception):
    pass


@dataclass
class SagaInput:
    fleet_id: str
    connector_id: str
    energy_kwh: float
    power_kw: int = 50
    fast_charge_boost: bool = False

    def fingerprint(self) -> str:
        raw = json.dumps({"fleet": self.fleet_id, "connector": self.connector_id, "kwh": str(self.energy_kwh), "boost": self.fast_charge_boost}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class SagaStore:
    """Persistencia del log de la SAGA en DynamoDB (tabla propia del orquestador)."""

    def __init__(self, table) -> None:
        self.table = table

    def create(self, saga: dict) -> bool:
        try:
            self.table.put_item(Item=saga, ConditionExpression="attribute_not_exists(saga_id)")
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get(self, saga_id: str) -> dict | None:
        return self.table.get_item(Key={"saga_id": saga_id}, ConsistentRead=True).get("Item")

    def save(self, saga: dict) -> None:
        saga["updated_at"] = now_iso()
        self.table.put_item(Item=saga)

    def recent(self, limit: int = 25) -> list[dict]:
        items, kwargs = [], {}
        while True:
            page = self.table.scan(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page or len(items) > 2000:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return sorted(items, key=lambda s: s.get("created_at", ""), reverse=True)[:limit]


class SagaOrchestrator:
    def __init__(self, store: SagaStore, clients: ServiceClients, environment: str, sleep=time.sleep) -> None:
        self.store = store
        self.clients = clients
        self.environment = environment
        self.sleep = sleep

    # ------------------------------------------------------------------ util
    def _event(self, saga: dict, step: str, action: str, status: str, detail: str | None = None, **extra) -> None:
        event = {"step": step, "action": action, "status": status, "at": now_iso()}
        if detail:
            event["detail"] = detail
        event.update({k: v for k, v in extra.items() if v is not None})
        saga.setdefault("events", []).append(event)
        self.store.save(saga)
        level = logging.ERROR if status in ("FAILED", "COMPENSATION_FAILED") else logging.INFO
        log.log(level, f"saga_{step.lower()}_{action.lower()}_{status.lower()}", extra={"saga_id": saga["saga_id"], "saga_status": saga["status"], "step": step, "action": action, "step_status": status, "detail": detail})

    def _set_status(self, saga: dict, status: str) -> None:
        saga["status"] = status
        self.store.save(saga)

    # ------------------------------------------------------------- entrada
    def start(self, data: SagaInput, idempotency_key: str | None = None) -> tuple[dict, bool]:
        saga_id = str(uuid.UUID(idempotency_key)) if idempotency_key else str(uuid.uuid4())
        token = correlation_id_var.set(saga_id)
        try:
            saga = {
                "saga_id": saga_id,
                "correlation_id": saga_id,
                "environment": self.environment,
                "status": "STARTED",
                "fleet_id": data.fleet_id,
                "connector_id": data.connector_id,
                "energy_kwh": str(data.energy_kwh),
                "power_kw": data.power_kw,
                "fast_charge_boost": data.fast_charge_boost,
                "fingerprint": data.fingerprint(),
                "completed_steps": [],
                "compensations": [],
                "events": [],
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            if not self.store.create(saga):
                existing = self.store.get(saga_id)
                if existing["fingerprint"] != data.fingerprint():
                    raise IdempotencyConflict("El idempotencyKey ya se usó con otros datos")
                log.info("saga_idempotent_replay", extra={"saga_id": saga_id, "saga_status": existing["status"]})
                return existing, True
            log.info("saga_started", extra={"saga_id": saga_id, "fleet_id": data.fleet_id, "connector_id": data.connector_id})
            self._run(saga, data)
            return saga, False
        finally:
            correlation_id_var.reset(token)

    # -------------------------------------------------------------- flujo
    def _run(self, saga: dict, data: SagaInput) -> None:
        sid = saga["saga_id"]

        # Paso 1 · reserva --------------------------------------------------
        self._event(saga, "RESERVATION", "RESERVE", "RUNNING")
        try:
            reservation = self.clients.reserve(sid, data.connector_id, data.fleet_id, data.fast_charge_boost)
            data.power_kw = int(reservation.get("power_kw") or data.power_kw)
            saga["power_kw"] = data.power_kw
            saga["completed_steps"].append("RESERVATION")
            saga["status"] = "RESERVED"
            self._event(saga, "RESERVATION", "RESERVE", "COMPLETED", f"Conector {data.connector_id} -> RESERVED")
        except BusinessFailure as exc:
            saga["failure_reason"] = exc.code
            saga["status"] = "REJECTED"
            self._event(saga, "RESERVATION", "RESERVE", "FAILED", exc.message, code=exc.code)
            return
        except TechnicalFailure as exc:
            saga["failure_reason"] = "RESERVATION_UNCERTAIN"
            self._event(saga, "RESERVATION", "RESERVE", "FAILED", str(exc), code="TECHNICAL")
            self._compensate(saga, ["RESERVATION"])
            return

        # Paso 2 · cobro ----------------------------------------------------
        self._event(saga, "BILLING", "CHARGE", "RUNNING")
        try:
            charge = self.clients.charge(sid, data.fleet_id, data.energy_kwh, data.power_kw)
            saga["completed_steps"].append("BILLING")
            saga["amount"] = str(charge.get("amount"))
            saga["surge_applied"] = bool(charge.get("surge_applied"))
            saga["status"] = "CHARGED"
            self._event(saga, "BILLING", "CHARGE", "COMPLETED", f"Cobrados {charge.get('amount')} COP", amount=str(charge.get("amount")))
        except BusinessFailure as exc:
            saga["failure_reason"] = exc.code
            self._event(saga, "BILLING", "CHARGE", "FAILED", exc.message, code=exc.code)
            self._compensate(saga, list(saga["completed_steps"]))
            return
        except TechnicalFailure as exc:
            saga["failure_reason"] = "BILLING_UNCERTAIN"
            self._event(saga, "BILLING", "CHARGE", "FAILED", str(exc), code="TECHNICAL")
            self._compensate(saga, saga["completed_steps"] + ["BILLING"])
            return

        # Paso 3 · activación física --------------------------------------
        self._event(saga, "DISPATCH", "START_SESSION", "RUNNING")
        try:
            self.clients.start_session(sid, data.connector_id, data.energy_kwh, data.power_kw)
            saga["completed_steps"].append("DISPATCH")
            self._event(saga, "DISPATCH", "START_SESSION", "COMPLETED", "Flujo eléctrico iniciado")
        except (BusinessFailure, TechnicalFailure) as exc:
            code = exc.code if isinstance(exc, BusinessFailure) else "DISPATCH_UNCERTAIN"
            saga["failure_reason"] = code
            self._event(saga, "DISPATCH", "START_SESSION", "FAILED", getattr(exc, "message", str(exc)), code=code)
            self._compensate(saga, list(saga["completed_steps"]))
            return

        # Cierre: la bahía pasa a CHARGING ---------------------------------
        for attempt in range(1, COMPENSATION_ATTEMPTS + 1):
            try:
                self.clients.confirm(sid)
                break
            except (BusinessFailure, TechnicalFailure) as exc:
                if attempt == COMPENSATION_ATTEMPTS:
                    self._event(saga, "RESERVATION", "CONFIRM", "WARNING", f"No se confirmó la reserva: {exc}")
                self.sleep(0.2 * attempt)
        saga["status"] = "COMPLETED"
        self._event(saga, "SAGA", "FINISH", "COMPLETED", "Reserva, cobro y activación confirmados")

    # ------------------------------------------------------- compensación
    def _compensate(self, saga: dict, steps: list[str]) -> None:
        saga["status"] = "COMPENSATING"
        saga["pending_compensations"] = [s for s in reversed(steps) if s in ("BILLING", "RESERVATION")]
        self.store.save(saga)
        reason = f"SAGA_COMPENSATION:{saga.get('failure_reason', 'UNKNOWN')}"
        while saga["pending_compensations"]:
            step = saga["pending_compensations"][0]
            action = "REFUND" if step == "BILLING" else "RELEASE"
            ok = False
            for attempt in range(1, COMPENSATION_ATTEMPTS + 1):
                self._event(saga, step, action, "RUNNING", f"Intento {attempt}")
                try:
                    result = self.clients.refund(saga["saga_id"], reason) if step == "BILLING" else self.clients.release(saga["saga_id"], reason)
                    saga["compensations"].append({"step": step, "action": action, "result": result.get("status"), "idempotent": bool(result.get("idempotent")), "at": now_iso()})
                    self._event(saga, step, action, "COMPENSATED", f"Resultado: {result.get('status')}")
                    ok = True
                    break
                except (BusinessFailure, TechnicalFailure) as exc:
                    self._event(saga, step, action, "RETRYING" if attempt < COMPENSATION_ATTEMPTS else "COMPENSATION_FAILED", str(exc))
                    self.sleep(0.25 * 2 ** (attempt - 1))
            if not ok:
                # No se continúa con las compensaciones anteriores: el orden inverso es obligatorio.
                saga["status"] = "COMPENSATION_FAILED"
                self._event(saga, "SAGA", "COMPENSATE", "COMPENSATION_FAILED", f"Falló la compensación de {step}; requiere retry_compensation")
                return
            saga["pending_compensations"].pop(0)
        saga["status"] = "COMPENSATED"
        self._event(saga, "SAGA", "COMPENSATE", "COMPENSATED", "Rollback distribuido completado")

    def retry_compensation(self, saga_id: str, stale_after: timedelta = timedelta(minutes=2)) -> dict:
        """Reanuda una SAGA en COMPENSATION_FAILED o huérfana (no terminal y sin cambios hace > stale_after)."""
        saga = self.store.get(saga_id)
        if not saga:
            raise KeyError(saga_id)
        token = correlation_id_var.set(saga_id)
        try:
            if saga["status"] in ("COMPLETED", "REJECTED", "COMPENSATED"):
                return saga
            updated = datetime.fromisoformat(saga["updated_at"])
            if saga["status"] != "COMPENSATION_FAILED" and datetime.now(UTC) - updated < stale_after:
                raise RuntimeError("La SAGA sigue en ejecución; espere antes de reanudarla")
            saga.setdefault("failure_reason", "ORPHAN_RECOVERY")
            steps = list(saga.get("completed_steps", []))
            if saga["status"] in ("STARTED", "RESERVED", "CHARGED"):
                # Huérfana: el último paso pudo quedar a medias -> se compensa también (idempotente).
                nxt = {"STARTED": "RESERVATION", "RESERVED": "BILLING"}.get(saga["status"])
                if nxt and nxt not in steps:
                    steps.append(nxt)
            elif saga.get("pending_compensations"):
                steps = list(reversed(saga["pending_compensations"]))
            self._event(saga, "SAGA", "RETRY_COMPENSATION", "RUNNING", f"Reanudando desde {saga['status']}")
            self._compensate(saga, steps)
            return saga
        finally:
            correlation_id_var.reset(token)


def serialize(saga: dict) -> dict:
    return to_jsonable(saga)
