"""E2E: los 4 servicios corren como servidores HTTP reales (uvicorn) y la prueba
habla SOLO GraphQL contra el gateway, igual que el frontend.

Cubre el flujo completo "Reservar y Activar Carga":
  * éxito,
  * fallo del cargador (flag) -> reembolso y liberación,
  * fondos insuficientes -> liberación,
  * idempotencia de la mutación,
  * cambio de flag en caliente vía GraphQL.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

MUTATION = """
mutation($input: ReserveChargeInput!) {
  reserveAndActivateCharge(input: $input) {
    success idempotent message
    saga { sagaId status failureReason amount completedSteps
           compensations { step action result }
           events { step action status } }
  }
}"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app) -> tuple[str, uvicorn.Server]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn no arrancó")
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


@pytest.fixture()
def stack(aws_env):
    from billing_wallet.main import app as billing_app
    from energy_dispatch.main import app as dispatch_app
    from graphql_gateway import state
    from graphql_gateway.clients import ServiceClients, ServiceUrls
    from graphql_gateway.main import app as gateway_app
    from station_reservation.main import app as reservation_app

    servers = []
    urls = {}
    for name, app in (("reservation", reservation_app), ("billing", billing_app), ("dispatch", dispatch_app)):
        url, server = _serve(app)
        urls[name] = url
        servers.append(server)
    state.reset(ServiceClients(ServiceUrls(**urls)))
    gateway_url, server = _serve(gateway_app)
    servers.append(server)
    client = httpx.Client(base_url=gateway_url, timeout=15)

    def gql(query: str, variables: dict | None = None) -> dict:
        response = client.post("/graphql", json={"query": query, "variables": variables or {}})
        response.raise_for_status()
        body = response.json()
        assert "errors" not in body, body
        return body["data"]

    yield gql
    for server in servers:
        server.should_exit = True


def wallet_balance(gql, fleet):
    return gql("query($f: ID!) { wallet(fleetId: $f) { balance } }", {"f": fleet})["wallet"]["balance"]


def connector(gql, connector_id):
    stations = gql("{ stations { connectors { connectorId status } } }")["stations"]
    return next(c["status"] for s in stations for c in s["connectors"] if c["connectorId"] == connector_id)


def set_flag(gql, name, enabled):
    return gql("mutation($n: String!, $e: Boolean!) { setFeatureFlag(name: $n, enabled: $e, actor: \"e2e\") { name enabled } }", {"n": name, "e": enabled})


def test_happy_path(stack):
    gql = stack
    set_flag(gql, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    start = wallet_balance(gql, "FLEET-RAPPI-EV")
    result = gql(MUTATION, {"input": {"fleetId": "FLEET-RAPPI-EV", "connectorId": "BOG80-C1", "energyKwh": 20}})["reserveAndActivateCharge"]
    assert result["success"] and result["saga"]["status"] == "COMPLETED"
    assert result["saga"]["completedSteps"] == ["RESERVATION", "BILLING", "DISPATCH"]
    assert connector(gql, "BOG80-C1") == "CHARGING"
    assert wallet_balance(gql, "FLEET-RAPPI-EV") == start - result["saga"]["amount"]


def test_hardware_fault_triggers_compensation_in_reverse_order(stack):
    gql = stack
    set_flag(gql, "SIMULATE_CHARGER_HARDWARE_FAULT", True)  # en caliente, sin redesplegar
    start = wallet_balance(gql, "FLEET-RAPPI-EV")
    result = gql(MUTATION, {"input": {"fleetId": "FLEET-RAPPI-EV", "connectorId": "BOG80-U1", "energyKwh": 30}})["reserveAndActivateCharge"]
    saga = result["saga"]
    assert not result["success"]
    assert saga["status"] == "COMPENSATED" and saga["failureReason"] == "CHARGER_HARDWARE_FAULT"
    assert [(c["step"], c["action"]) for c in saga["compensations"]] == [("BILLING", "REFUND"), ("RESERVATION", "RELEASE")]
    assert wallet_balance(gql, "FLEET-RAPPI-EV") == start
    assert connector(gql, "BOG80-U1") == "AVAILABLE"

    history = gql("{ sagas(limit: 5) { sagaId status } }")["sagas"]
    assert any(s["sagaId"] == saga["sagaId"] and s["status"] == "COMPENSATED" for s in history)


def test_insufficient_funds_releases_connector(stack):
    gql = stack
    set_flag(gql, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    result = gql(MUTATION, {"input": {"fleetId": "FLEET-LOWBAL", "connectorId": "MDEP-C1", "energyKwh": 50}})["reserveAndActivateCharge"]
    saga = result["saga"]
    assert saga["status"] == "COMPENSATED" and saga["failureReason"] == "INSUFFICIENT_FUNDS"
    assert [(c["step"], c["action"]) for c in saga["compensations"]] == [("RESERVATION", "RELEASE")]
    assert connector(gql, "MDEP-C1") == "AVAILABLE"


def test_mutation_is_idempotent(stack):
    gql = stack
    set_flag(gql, "SIMULATE_CHARGER_HARDWARE_FAULT", False)
    key = str(uuid.uuid4())
    payload = {"input": {"fleetId": "FLEET-SERVI-EV", "connectorId": "CLOA-C2", "energyKwh": 10, "idempotencyKey": key}}
    start = wallet_balance(gql, "FLEET-SERVI-EV")
    first = gql(MUTATION, payload)["reserveAndActivateCharge"]
    second = gql(MUTATION, payload)["reserveAndActivateCharge"]
    assert first["saga"]["sagaId"] == second["saga"]["sagaId"] == key
    assert second["idempotent"] is True
    assert wallet_balance(gql, "FLEET-SERVI-EV") == start - first["saga"]["amount"]  # un solo cobro


def test_environment_and_flags_query(stack):
    gql = stack
    data = gql("{ environment { name tables } featureFlags { name enabled environment } }")
    assert data["environment"]["name"] == "test"
    assert data["environment"]["tables"]["billing"] == "aegisci-billing-test"
    assert {f["name"] for f in data["featureFlags"]} == {"ENABLE_DYNAMIC_SURGE_PRICING", "ENABLE_FAST_CHARGE_BOOST", "SIMULATE_CHARGER_HARDWARE_FAULT"}
