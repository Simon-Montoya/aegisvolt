"""Datos semilla idempotentes: electrolineras, conectores, billeteras de flotas y flags.

Solo inserta lo que no existe (attribute_not_exists): se puede ejecutar en cada
despliegue sin pisar el estado de las reservas ni los saldos.
"""

from __future__ import annotations

from decimal import Decimal

from botocore.exceptions import ClientError

from . import now_iso
from .config import Settings
from .flags import KNOWN_FLAGS

STATIONS = [
    ("BOG-CALLE80", "Electrolinera Calle 80", "Bogotá", [("BOG80-C1", 50, "CCS2"), ("BOG80-C2", 50, "CCS2"), ("BOG80-U1", 180, "CCS2"), ("BOG80-U2", 350, "CCS2")]),
    ("MDE-PALACE", "Hub Logístico Palacé", "Medellín", [("MDEP-C1", 60, "CCS2"), ("MDEP-C2", 60, "CHAdeMO"), ("MDEP-U1", 150, "CCS2")]),
    ("CLO-ACOPI", "Patio Acopi Yumbo", "Cali", [("CLOA-C1", 22, "Type2"), ("CLOA-C2", 50, "CCS2")]),
]

WALLETS = [
    ("FLEET-RAPPI-EV", "Reparto Urbano Norte", Decimal("2500000")),
    ("FLEET-SERVI-EV", "ServiExpress Última Milla", Decimal("800000")),
    ("FLEET-LOWBAL", "Flota Demo Saldo Bajo", Decimal("5000")),
]

# Valores iniciales por ambiente (Terraform los crea igual; esto cubre pruebas/CI).
DEFAULT_FLAGS = {
    "dev": {"ENABLE_DYNAMIC_SURGE_PRICING": True, "ENABLE_FAST_CHARGE_BOOST": True, "SIMULATE_CHARGER_HARDWARE_FAULT": False},
    "test": {"ENABLE_DYNAMIC_SURGE_PRICING": True, "ENABLE_FAST_CHARGE_BOOST": True, "SIMULATE_CHARGER_HARDWARE_FAULT": False},
    "prod": {"ENABLE_DYNAMIC_SURGE_PRICING": False, "ENABLE_FAST_CHARGE_BOOST": False, "SIMULATE_CHARGER_HARDWARE_FAULT": False},
}


def _put_if_absent(table, item: dict, key_attr: str = "pk") -> bool:
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(#k)", ExpressionAttributeNames={"#k": key_attr})
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def seed(dynamodb, settings: Settings, include_flags: bool = True) -> dict:
    counts = {"connectors": 0, "wallets": 0, "flags": 0}
    reservations = dynamodb.Table(settings.table_name("reservations"))
    for station_id, name, city, connectors in STATIONS:
        for connector_id, power, ctype in connectors:
            counts["connectors"] += _put_if_absent(
                reservations,
                {
                    "pk": f"CONNECTOR#{connector_id}",
                    "sk": "META",
                    "station_id": station_id,
                    "station_name": name,
                    "city": city,
                    "connector_id": connector_id,
                    "power_kw": power,
                    "connector_type": ctype,
                    "status": "AVAILABLE",
                    "updated_at": now_iso(),
                },
            )
    billing = dynamodb.Table(settings.table_name("billing"))
    for fleet_id, name, balance in WALLETS:
        counts["wallets"] += _put_if_absent(billing, {"pk": f"WALLET#{fleet_id}", "sk": "META", "fleet_id": fleet_id, "name": name, "balance": balance, "currency": "COP", "created_at": now_iso()})
    if include_flags:
        flags = dynamodb.Table(settings.table_name("feature-flags"))
        for flag, enabled in DEFAULT_FLAGS[settings.environment].items():
            counts["flags"] += _put_if_absent(flags, {"name": flag, "enabled": enabled, "description": KNOWN_FLAGS[flag], "updated_at": now_iso(), "updated_by": "seed"}, key_attr="name")
    return counts
