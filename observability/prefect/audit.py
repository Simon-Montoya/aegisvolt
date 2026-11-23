"""Auditoría de consistencia de las SAGAs (lógica pura, sin Prefect, testeable).

Para cada SAGA del log del orquestador (aegisvolt-sagas-<env>) se comprueba que
el estado de las tablas de los microservicios coincide con el estado final:

  COMPLETED   -> reserva CONFIRMED, cobro CHARGED, sesión ACTIVE/COMPLETED
  COMPENSATED -> reserva RELEASED/VOIDED/ausente y cobro REFUNDED/VOIDED/REJECTED/ausente
  REJECTED    -> sin cobro aplicado
  no terminal y sin cambios hace > N minutos -> HUÉRFANA
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

TERMINAL = {"COMPLETED", "REJECTED", "COMPENSATED", "COMPENSATION_FAILED"}


@dataclass
class SagaAudit:
    environment: str
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    orphans: list[dict] = field(default_factory=list)
    inconsistencies: list[dict] = field(default_factory=list)
    compensated: list[dict] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.orphans and not self.inconsistencies and self.by_status.get("COMPENSATION_FAILED", 0) == 0


def _status(item: dict | None) -> str | None:
    return item.get("status") if item else None


def check_saga(saga: dict, reservation: dict | None, charge: dict | None, session: dict | None) -> list[str]:
    """Devuelve la lista de problemas de consistencia (vacía = consistente)."""
    problems = []
    status = saga["status"]
    r, c, s = _status(reservation), _status(charge), _status(session)
    if status == "COMPLETED":
        if r != "CONFIRMED":
            problems.append(f"reserva en {r}, se esperaba CONFIRMED")
        if c != "CHARGED":
            problems.append(f"cobro en {c}, se esperaba CHARGED")
        if s not in ("ACTIVE", "COMPLETED"):
            problems.append(f"sesión en {s}, se esperaba ACTIVE")
    elif status == "COMPENSATED":
        if r not in (None, "RELEASED", "VOIDED"):
            problems.append(f"reserva en {r}: el conector no fue liberado")
        if c not in (None, "REFUNDED", "VOIDED", "REJECTED"):
            problems.append(f"cobro en {c}: el saldo no fue reembolsado")
    elif status == "REJECTED":
        if c == "CHARGED":
            problems.append("SAGA rechazada con cobro aplicado")
    return problems


def audit(environment: str, sagas: list[dict], lookup, orphan_after: timedelta = timedelta(minutes=5), now: datetime | None = None) -> SagaAudit:
    """lookup(saga_id) -> (reservation, charge, session)."""
    now = now or datetime.now(UTC)
    result = SagaAudit(environment=environment, total=len(sagas))
    for saga in sagas:
        status = saga["status"]
        result.by_status[status] = result.by_status.get(status, 0) + 1
        summary = {"saga_id": saga["saga_id"], "status": status, "failure_reason": saga.get("failure_reason"), "updated_at": saga.get("updated_at")}
        if status not in TERMINAL:
            updated = datetime.fromisoformat(saga["updated_at"])
            if now - updated > orphan_after:
                result.orphans.append({**summary, "minutes_stuck": round((now - updated).total_seconds() / 60, 1)})
            continue
        if status == "COMPENSATED":
            result.compensated.append(summary)
        problems = check_saga(saga, *lookup(saga["saga_id"]))
        if problems:
            result.inconsistencies.append({**summary, "problems": problems})
    return result
