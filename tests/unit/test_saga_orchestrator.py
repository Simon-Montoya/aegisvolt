"""Pruebas unitarias del orquestador: orden de las compensaciones e idempotencia.

No usan DynamoDB ni HTTP: los servicios se reemplazan por dobles en memoria.
"""

from __future__ import annotations

import uuid

import pytest

from graphql_gateway.clients import BusinessFailure, TechnicalFailure
from graphql_gateway.saga import IdempotencyConflict, SagaInput, SagaOrchestrator, SagaStore


class MemoryStore(SagaStore):
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def create(self, saga):
        if saga["saga_id"] in self.items:
            return False
        self.items[saga["saga_id"]] = saga
        return True

    def get(self, saga_id):
        return self.items.get(saga_id)

    def save(self, saga):
        self.items[saga["saga_id"]] = saga

    def recent(self, limit=25):
        return list(self.items.values())[:limit]


class FakeClients:
    """Registra el orden de las llamadas y permite inyectar fallos por operación."""

    def __init__(self, **failures) -> None:
        self.calls: list[str] = []
        self.failures = failures  # operación -> excepción o lista de excepciones

    def _maybe_fail(self, op):
        self.calls.append(op)
        failure = self.failures.get(op)
        if isinstance(failure, list):
            failure = failure.pop(0) if failure else None
        if failure:
            raise failure

    def reserve(self, saga_id, connector_id, fleet_id, boost):
        self._maybe_fail("reserve")
        return {"status": "RESERVED", "power_kw": 180}

    def charge(self, saga_id, fleet_id, kwh, power):
        self._maybe_fail("charge")
        return {"status": "CHARGED", "amount": 49500, "surge_applied": False}

    def start_session(self, saga_id, connector_id, kwh, power):
        self._maybe_fail("start_session")
        return {"status": "ACTIVE"}

    def confirm(self, saga_id):
        self._maybe_fail("confirm")
        return {"status": "CONFIRMED"}

    def refund(self, saga_id, reason):
        self._maybe_fail("refund")
        return {"status": "REFUNDED"}

    def release(self, saga_id, reason):
        self._maybe_fail("release")
        return {"status": "RELEASED"}


DATA = SagaInput(fleet_id="FLEET-1", connector_id="C-1", energy_kwh=30)


def make(**failures):
    clients = FakeClients(**failures)
    return SagaOrchestrator(MemoryStore(), clients, "test", sleep=lambda _: None), clients


def test_happy_path_completes_all_steps():
    orch, clients = make()
    saga, idempotent = orch.start(DATA)
    assert saga["status"] == "COMPLETED"
    assert not idempotent
    assert clients.calls == ["reserve", "charge", "start_session", "confirm"]
    assert saga["completed_steps"] == ["RESERVATION", "BILLING", "DISPATCH"]


def test_step3_failure_refunds_then_releases_in_reverse_order():
    orch, clients = make(start_session=BusinessFailure(502, "CHARGER_HARDWARE_FAULT", "falla"))
    saga, _ = orch.start(DATA)
    assert saga["status"] == "COMPENSATED"
    assert saga["failure_reason"] == "CHARGER_HARDWARE_FAULT"
    assert clients.calls == ["reserve", "charge", "start_session", "refund", "release"]
    assert [c["action"] for c in saga["compensations"]] == ["REFUND", "RELEASE"]


def test_step2_insufficient_funds_only_releases_connector():
    orch, clients = make(charge=BusinessFailure(402, "INSUFFICIENT_FUNDS", "sin saldo"))
    saga, _ = orch.start(DATA)
    assert saga["status"] == "COMPENSATED"
    assert clients.calls == ["reserve", "charge", "release"]


def test_step1_business_rejection_has_nothing_to_compensate():
    orch, clients = make(reserve=BusinessFailure(409, "CONNECTOR_UNAVAILABLE", "ocupado"))
    saga, _ = orch.start(DATA)
    assert saga["status"] == "REJECTED"
    assert clients.calls == ["reserve"]


def test_uncertain_charge_is_also_compensated():
    """Timeout en el cobro: no se sabe si se descontó -> se reembolsa (idempotente) y se libera."""
    orch, clients = make(charge=TechnicalFailure("timeout"))
    saga, _ = orch.start(DATA)
    assert saga["status"] == "COMPENSATED"
    assert clients.calls == ["reserve", "charge", "refund", "release"]


def test_same_idempotency_key_returns_same_saga_without_new_calls():
    orch, clients = make()
    key = str(uuid.uuid4())
    first, _ = orch.start(DATA, key)
    calls = list(clients.calls)
    second, idempotent = orch.start(DATA, key)
    assert idempotent
    assert second["saga_id"] == first["saga_id"] == key
    assert clients.calls == calls


def test_same_key_with_different_payload_is_rejected():
    orch, _ = make()
    key = str(uuid.uuid4())
    orch.start(DATA, key)
    with pytest.raises(IdempotencyConflict):
        orch.start(SagaInput(fleet_id="OTRA", connector_id="C-1", energy_kwh=30), key)


def test_failed_refund_stops_and_does_not_release_out_of_order():
    always = [TechnicalFailure("down")] * 3
    orch, clients = make(start_session=BusinessFailure(502, "CHARGER_HARDWARE_FAULT", "x"), refund=list(always))
    saga, _ = orch.start(DATA)
    assert saga["status"] == "COMPENSATION_FAILED"
    assert clients.calls.count("refund") == 3
    assert "release" not in clients.calls
    assert saga["pending_compensations"] == ["BILLING", "RESERVATION"]

    # El servicio se recupera: retry_compensation termina en orden (refund y luego release).
    clients.failures.clear()
    saga = orch.retry_compensation(saga["saga_id"])
    assert saga["status"] == "COMPENSATED"
    assert clients.calls[-2:] == ["refund", "release"]


def test_compensation_retries_transient_errors():
    orch, clients = make(start_session=BusinessFailure(502, "CHARGER_HARDWARE_FAULT", "x"), release=[TechnicalFailure("blip")])
    saga, _ = orch.start(DATA)
    assert saga["status"] == "COMPENSATED"
    assert clients.calls.count("release") == 2
