"""Pruebas de integración: cada microservicio contra sus tablas DynamoDB reales (moto o Floci)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


def sid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def reservation(aws_env):
    from station_reservation.main import app

    return TestClient(app)


@pytest.fixture()
def billing(aws_env):
    from billing_wallet.main import app

    return TestClient(app)


@pytest.fixture()
def dispatch(aws_env):
    from energy_dispatch.main import app

    return TestClient(app)


def set_flag(aws_env, name, enabled):
    aws_env["dynamodb"].Table(aws_env["settings"].table_name("feature-flags")).put_item(Item={"name": name, "enabled": enabled})


def connector_status(client, connector_id):
    for station in client.get("/stations").json():
        for c in station["connectors"]:
            if c["connector_id"] == connector_id:
                return c["status"]
    raise AssertionError(connector_id)


def balance(client, fleet):
    return client.get(f"/wallets/{fleet}").json()["balance"]


# ------------------------------------------------------------ reservas
def test_reserve_is_idempotent_and_locks_connector(reservation):
    saga = sid()
    body = {"saga_id": saga, "connector_id": "BOG80-C1", "fleet_id": "FLEET-RAPPI-EV"}
    first = reservation.post("/reservations", json=body)
    assert first.status_code == 201 and first.json()["status"] == "RESERVED"
    again = reservation.post("/reservations", json=body)
    assert again.status_code == 201 and again.json()["idempotent"] is True
    assert connector_status(reservation, "BOG80-C1") == "RESERVED"

    other = reservation.post("/reservations", json={**body, "saga_id": sid()})
    assert other.status_code == 409 and other.json()["code"] == "CONNECTOR_UNAVAILABLE"


def test_release_is_idempotent(reservation):
    saga = sid()
    reservation.post("/reservations", json={"saga_id": saga, "connector_id": "BOG80-C2", "fleet_id": "F"})
    r1 = reservation.post(f"/reservations/{saga}/release")
    r2 = reservation.post(f"/reservations/{saga}/release")
    assert r1.json()["status"] == "RELEASED" and r1.json()["idempotent"] is False
    assert r2.json()["status"] == "RELEASED" and r2.json()["idempotent"] is True
    assert connector_status(reservation, "BOG80-C2") == "AVAILABLE"


def test_release_before_reserve_leaves_tombstone_that_blocks_late_request(reservation):
    saga = sid()
    voided = reservation.post(f"/reservations/{saga}/release")
    assert voided.json()["status"] == "VOIDED"
    late = reservation.post("/reservations", json={"saga_id": saga, "connector_id": "CLOA-C1", "fleet_id": "F"})
    assert late.status_code == 409 and late.json()["code"] == "RESERVATION_ALREADY_COMPENSATED"
    assert connector_status(reservation, "CLOA-C1") == "AVAILABLE"


def test_fast_charge_boost_respects_flag(reservation, aws_env):
    set_flag(aws_env, "ENABLE_FAST_CHARGE_BOOST", False)
    off = reservation.post("/reservations", json={"saga_id": sid(), "connector_id": "BOG80-U1", "fleet_id": "F", "fast_charge_boost": True})
    assert off.status_code == 422 and off.json()["code"] == "FEATURE_DISABLED"
    set_flag(aws_env, "ENABLE_FAST_CHARGE_BOOST", True)  # sin redesplegar
    on = reservation.post("/reservations", json={"saga_id": sid(), "connector_id": "BOG80-U1", "fleet_id": "F", "fast_charge_boost": True})
    assert on.status_code == 201 and on.json()["priority"] == "HIGH"


# ------------------------------------------------------------ billetera
def test_charge_and_refund_are_applied_exactly_once(billing):
    fleet = "FLEET-RAPPI-EV"
    start = balance(billing, fleet)
    saga = sid()
    body = {"saga_id": saga, "fleet_id": fleet, "energy_kwh": "10", "power_kw": 50}
    c1 = billing.post("/charges", json=body)
    c2 = billing.post("/charges", json=body)
    assert c1.status_code == 201 and c2.json()["idempotent"] is True
    amount = c1.json()["amount"]
    assert balance(billing, fleet) == start - amount

    billing.post(f"/charges/{saga}/refund")
    again = billing.post(f"/charges/{saga}/refund")
    assert again.json()["idempotent"] is True
    assert balance(billing, fleet) == start
    ledger_types = [e["type"] for e in billing.get(f"/wallets/{fleet}").json()["ledger"] if e["saga_id"] == saga]
    assert sorted(ledger_types) == ["DEBIT", "REFUND"]


def test_insufficient_funds_keeps_balance(billing):
    start = balance(billing, "FLEET-LOWBAL")
    r = billing.post("/charges", json={"saga_id": sid(), "fleet_id": "FLEET-LOWBAL", "energy_kwh": "40", "power_kw": 50})
    assert r.status_code == 402 and r.json()["code"] == "INSUFFICIENT_FUNDS"
    assert balance(billing, "FLEET-LOWBAL") == start


def test_late_charge_after_void_is_blocked(billing):
    saga = sid()
    start = balance(billing, "FLEET-SERVI-EV")
    assert billing.post(f"/charges/{saga}/refund").json()["status"] == "VOIDED"
    late = billing.post("/charges", json={"saga_id": saga, "fleet_id": "FLEET-SERVI-EV", "energy_kwh": "5", "power_kw": 50})
    assert late.status_code == 409
    assert balance(billing, "FLEET-SERVI-EV") == start


def test_surge_pricing_flag_changes_price_without_redeploy(billing, aws_env, monkeypatch):
    monkeypatch.setenv("SURGE_FORCE_PEAK", "true")
    body = {"fleet_id": "FLEET-RAPPI-EV", "energy_kwh": "10", "power_kw": 50}
    set_flag(aws_env, "ENABLE_DYNAMIC_SURGE_PRICING", False)
    normal = billing.post("/charges", json={**body, "saga_id": sid()}).json()
    set_flag(aws_env, "ENABLE_DYNAMIC_SURGE_PRICING", True)
    surged = billing.post("/charges", json={**body, "saga_id": sid()}).json()
    assert surged["amount"] == normal["amount"] * 1.5 and surged["surge_applied"] is True


# ------------------------------------------------------------ despacho
def test_dispatch_fault_flag_returns_502_and_records_failure(dispatch, aws_env):
    set_flag(aws_env, "SIMULATE_CHARGER_HARDWARE_FAULT", True)
    saga = sid()
    r = dispatch.post("/sessions", json={"saga_id": saga, "connector_id": "BOG80-C1", "energy_kwh": "10", "power_kw": 50})
    assert r.status_code == 502 and r.json()["code"] == "CHARGER_HARDWARE_FAULT"
    assert dispatch.get(f"/sessions/{saga}").json()["status"] == "FAILED"


def test_dispatch_starts_session_with_telemetry(dispatch, aws_env):
    set_flag(aws_env, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    saga = sid()
    r = dispatch.post("/sessions", json={"saga_id": saga, "connector_id": "MDEP-C1", "energy_kwh": "10", "power_kw": 60})
    assert r.status_code == 201 and r.json()["status"] == "ACTIVE"
    points = dispatch.get("/telemetry/MDEP-C1").json()
    assert points and points[0]["saga_id"] == saga


# ------------------------------------------------------------ flags por ambiente
def test_flags_are_isolated_per_environment(aws_env):
    from aegis_common.config import Settings
    from aegis_common.flags import FeatureFlags

    test_flags = FeatureFlags(Settings(service_name="x", environment="test", project="aegisci"))
    prod_flags = FeatureFlags(Settings(service_name="x", environment="prod", project="aegisci"))
    assert prod_flags.table.name == "aegisci-feature-flags-prod"
    test_flags.set("ENABLE_DYNAMIC_SURGE_PRICING", False)
    assert test_flags.is_enabled("ENABLE_DYNAMIC_SURGE_PRICING") is False
    with pytest.raises(PermissionError):
        prod_flags.set("SIMULATE_CHARGER_HARDWARE_FAULT", True)
