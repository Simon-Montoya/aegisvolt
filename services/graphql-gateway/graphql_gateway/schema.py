"""Esquema GraphQL unificado de AegisVolt (capa de agregación).

Queries  -> consultar estaciones, telemetría, billeteras, flags y transacciones.
Mutations -> iniciar la SAGA (reserva + pago + sesión de carga), reanudar
             compensaciones, cambiar feature flags y finalizar una carga.
"""

from __future__ import annotations

import enum

import strawberry
from strawberry.scalars import JSON

from aegis_common import to_jsonable

from . import state
from .clients import BusinessFailure, TechnicalFailure
from .saga import IdempotencyConflict, SagaInput


@strawberry.enum
class ConnectorStatus(enum.Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    CHARGING = "CHARGING"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


@strawberry.enum
class SagaStatus(enum.Enum):
    STARTED = "STARTED"
    RESERVED = "RESERVED"
    CHARGED = "CHARGED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"


@strawberry.type
class Connector:
    connector_id: strawberry.ID
    power_kw: int
    connector_type: str | None
    status: ConnectorStatus
    saga_id: str | None
    updated_at: str | None


@strawberry.type
class Station:
    station_id: strawberry.ID
    name: str | None
    city: str | None
    connectors: list[Connector]

    @strawberry.field
    def available_connectors(self) -> int:
        return sum(1 for c in self.connectors if c.status == ConnectorStatus.AVAILABLE)


@strawberry.type
class TelemetryPoint:
    connector_id: str
    saga_id: str | None
    power_kw: float
    voltage_v: float
    temperature_c: float
    energy_delivered_kwh: float
    recorded_at: str


@strawberry.type
class LedgerEntry:
    saga_id: str
    type: str
    amount: float
    note: str | None
    created_at: str


@strawberry.type
class Wallet:
    fleet_id: strawberry.ID
    name: str | None
    balance: float
    currency: str
    ledger: list[LedgerEntry]


@strawberry.type
class FeatureFlag:
    name: str
    enabled: bool
    description: str | None
    environment: str
    updated_at: str | None
    updated_by: str | None


@strawberry.type
class SagaEvent:
    step: str
    action: str
    status: str
    at: str
    detail: str | None
    code: str | None


@strawberry.type
class Compensation:
    step: str
    action: str
    result: str | None
    idempotent: bool
    at: str


@strawberry.type
class SagaTransaction:
    saga_id: strawberry.ID
    correlation_id: str
    environment: str
    status: SagaStatus
    fleet_id: str
    connector_id: str
    energy_kwh: float
    amount: float | None
    surge_applied: bool | None
    failure_reason: str | None
    completed_steps: list[str]
    compensations: list[Compensation]
    events: list[SagaEvent]
    created_at: str
    updated_at: str


@strawberry.type
class SagaResult:
    success: bool
    idempotent: bool
    message: str
    saga: SagaTransaction | None


@strawberry.type
class EnvironmentInfo:
    name: str
    version: str
    track: str
    region: str
    tables: JSON
    services: JSON


@strawberry.input
class ReserveChargeInput:
    fleet_id: str
    connector_id: str
    energy_kwh: float
    fast_charge_boost: bool = False
    idempotency_key: str | None = strawberry.field(default=None, description="UUID; repetir la mutación con el mismo valor es idempotente")


# --------------------------------------------------------------------- mapeo
def _station(raw: dict) -> Station:
    return Station(
        station_id=raw["station_id"],
        name=raw.get("name"),
        city=raw.get("city"),
        connectors=[
            Connector(
                connector_id=c["connector_id"],
                power_kw=int(c.get("power_kw") or 0),
                connector_type=c.get("connector_type"),
                status=ConnectorStatus(c["status"]),
                saga_id=c.get("saga_id"),
                updated_at=c.get("updated_at"),
            )
            for c in raw.get("connectors", [])
        ],
    )


def _saga(raw: dict) -> SagaTransaction:
    raw = to_jsonable(raw)
    return SagaTransaction(
        saga_id=raw["saga_id"],
        correlation_id=raw.get("correlation_id", raw["saga_id"]),
        environment=raw.get("environment", ""),
        status=SagaStatus(raw["status"]),
        fleet_id=raw["fleet_id"],
        connector_id=raw["connector_id"],
        energy_kwh=float(raw.get("energy_kwh", 0)),
        amount=float(raw["amount"]) if raw.get("amount") not in (None, "None") else None,
        surge_applied=raw.get("surge_applied"),
        failure_reason=raw.get("failure_reason"),
        completed_steps=raw.get("completed_steps", []),
        compensations=[Compensation(step=c["step"], action=c["action"], result=c.get("result"), idempotent=bool(c.get("idempotent")), at=c["at"]) for c in raw.get("compensations", [])],
        events=[SagaEvent(step=e["step"], action=e["action"], status=e["status"], at=e["at"], detail=e.get("detail"), code=e.get("code")) for e in raw.get("events", [])],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
    )


def _flag(raw: dict) -> FeatureFlag:
    return FeatureFlag(**{k: raw.get(k) for k in ("name", "enabled", "description", "environment", "updated_at", "updated_by")})


# -------------------------------------------------------------------- raíz
@strawberry.type
class Query:
    @strawberry.field(description="Estaciones con el estado de cada conector")
    def stations(self) -> list[Station]:
        return [_station(s) for s in state.clients().stations()]

    @strawberry.field
    def station(self, station_id: strawberry.ID) -> Station | None:
        return next((_station(s) for s in state.clients().stations() if s["station_id"] == station_id), None)

    @strawberry.field(description="Últimas lecturas de telemetría del conector")
    def telemetry(self, connector_id: strawberry.ID, limit: int = 20) -> list[TelemetryPoint]:
        points = to_jsonable(state.clients().telemetry(str(connector_id), limit))
        return [TelemetryPoint(**{k: p.get(k) for k in TelemetryPoint.__annotations__}) for p in points]

    @strawberry.field
    def wallet(self, fleet_id: strawberry.ID) -> Wallet | None:
        try:
            raw = state.clients().wallet(str(fleet_id))
        except BusinessFailure:
            return None
        return Wallet(
            fleet_id=raw["fleet_id"],
            name=raw.get("name"),
            balance=float(raw["balance"]),
            currency=raw.get("currency", "COP"),
            ledger=[LedgerEntry(saga_id=e["saga_id"], type=e["type"], amount=float(e["amount"]), note=e.get("note"), created_at=e["created_at"]) for e in raw.get("ledger", [])],
        )

    @strawberry.field(description="Feature flags del ambiente actual (tabla aegisvolt-feature-flags-<env>)")
    def feature_flags(self) -> list[FeatureFlag]:
        return [_flag(f) for f in state.flags().all()]

    @strawberry.field(description="Historial de transacciones SAGA")
    def sagas(self, limit: int = 25) -> list[SagaTransaction]:
        return [_saga(s) for s in state.store().recent(limit)]

    @strawberry.field
    def saga(self, saga_id: strawberry.ID) -> SagaTransaction | None:
        raw = state.store().get(str(saga_id))
        return _saga(raw) if raw else None

    @strawberry.field
    def environment(self) -> EnvironmentInfo:
        s = state.settings
        return EnvironmentInfo(
            name=s.environment,
            version=s.app_version,
            track=s.deploy_track,
            region=s.aws_region,
            tables={k: s.table_name(k) for k in ("reservations", "billing", "dispatch", "sagas", "feature-flags")},
            services=state.clients().health(),
        )


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Dispara la SAGA: reserva (1) -> cobro (2) -> activación del cargador (3)")
    def reserve_and_activate_charge(self, input: ReserveChargeInput) -> SagaResult:
        data = SagaInput(fleet_id=input.fleet_id, connector_id=input.connector_id, energy_kwh=input.energy_kwh, fast_charge_boost=input.fast_charge_boost)
        try:
            saga, idempotent = state.orchestrator().start(data, input.idempotency_key)
        except IdempotencyConflict as exc:
            return SagaResult(success=False, idempotent=True, message=str(exc), saga=None)
        except ValueError:
            return SagaResult(success=False, idempotent=False, message="idempotencyKey debe ser un UUID", saga=None)
        status = saga["status"]
        messages = {
            "COMPLETED": "Carga activada",
            "REJECTED": f"Reserva rechazada: {saga.get('failure_reason')}",
            "COMPENSATED": f"Falló la SAGA ({saga.get('failure_reason')}); se ejecutaron las compensaciones",
            "COMPENSATION_FAILED": "Falló una compensación; la SAGA requiere reintento",
        }
        return SagaResult(success=status == "COMPLETED", idempotent=idempotent, message=messages.get(status, status), saga=_saga(saga))

    @strawberry.mutation(description="Reanuda las compensaciones de una SAGA huérfana o en COMPENSATION_FAILED")
    def retry_compensation(self, saga_id: strawberry.ID) -> SagaResult:
        try:
            saga = state.orchestrator().retry_compensation(str(saga_id))
        except KeyError:
            return SagaResult(success=False, idempotent=False, message="SAGA no encontrada", saga=None)
        except RuntimeError as exc:
            return SagaResult(success=False, idempotent=False, message=str(exc), saga=None)
        return SagaResult(success=saga["status"] == "COMPENSATED", idempotent=False, message=saga["status"], saga=_saga(saga))

    @strawberry.mutation(description="Cambia una feature flag del ambiente actual en caliente (sin redesplegar)")
    def set_feature_flag(self, name: str, enabled: bool, actor: str = "frontend") -> FeatureFlag:
        return _flag(state.flags().set(name, enabled, actor))

    @strawberry.mutation(description="Finaliza la carga de un conector (CHARGING -> AVAILABLE)")
    def finish_charging(self, connector_id: strawberry.ID) -> bool:
        try:
            state.clients().finish(str(connector_id))
            return True
        except (BusinessFailure, TechnicalFailure):
            return False


schema = strawberry.Schema(query=Query, mutation=Mutation)
