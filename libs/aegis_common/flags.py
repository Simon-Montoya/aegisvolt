"""Feature Flags propias sobre DynamoDB, una tabla por ambiente.

Tabla: aegisvolt-feature-flags-<env>   (hash key: name)

Como cada ambiente lee SOLO su tabla, cambiar una flag en dev no puede afectar
a prod. Los servicios leen la flag en cada petición con un caché corto
(FLAGS_CACHE_SECONDS, 3 s por defecto): cambiar el valor en DynamoDB modifica
el comportamiento en caliente, sin redesplegar código.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

from . import aws
from .config import Settings

log = logging.getLogger(__name__)

KNOWN_FLAGS: dict[str, str] = {
    "ENABLE_DYNAMIC_SURGE_PRICING": "Tarifa dinámica (multiplicador) en horas pico",
    "ENABLE_FAST_CHARGE_BOOST": "Reserva prioritaria de ultra-carga (>= 150 kW)",
    "SIMULATE_CHARGER_HARDWARE_FAULT": "Inyección de fallo físico del cargador (dispara el rollback de la SAGA)",
}

# Flags que jamás pueden activarse en producción (inyección de fallos).
PROD_LOCKED_FLAGS = {"SIMULATE_CHARGER_HARDWARE_FAULT"}


class FeatureFlags:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, dict] = {}
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    @property
    def table(self):
        return aws.table(self.settings, "feature-flags")

    def _refresh(self, force: bool = False) -> None:
        with self._lock:
            if not force and time.monotonic() - self._loaded_at < self.settings.flags_cache_seconds:
                return
            try:
                items = self.table.scan().get("Items", [])
                self._cache = {item["name"]: item for item in items}
                self._loaded_at = time.monotonic()
            except Exception as exc:  # noqa: BLE001
                # Si no se puede leer, se conserva el último valor conocido (fail-safe).
                log.warning("No se pudieron leer las feature flags: %s", exc)

    def is_enabled(self, name: str, default: bool = False) -> bool:
        self._refresh()
        item = self._cache.get(name)
        if item is None:
            return default
        return bool(item.get("enabled", default))

    def all(self) -> list[dict]:
        self._refresh(force=True)
        result = []
        for name, description in KNOWN_FLAGS.items():
            item = self._cache.get(name, {})
            result.append(
                {
                    "name": name,
                    "enabled": bool(item.get("enabled", False)),
                    "description": item.get("description", description),
                    "updated_at": item.get("updated_at"),
                    "updated_by": item.get("updated_by"),
                    "environment": self.settings.environment,
                }
            )
        return result

    def set(self, name: str, enabled: bool, actor: str = "graphql") -> dict:
        if name not in KNOWN_FLAGS:
            raise ValueError(f"Flag desconocida: {name}")
        if enabled and self.settings.environment == "prod" and name in PROD_LOCKED_FLAGS:
            raise PermissionError(f"{name} no se puede activar en prod")
        now = datetime.now(UTC).isoformat()
        self.table.update_item(
            Key={"name": name},
            UpdateExpression="SET enabled = :e, updated_at = :t, updated_by = :a, description = if_not_exists(description, :d)",
            ExpressionAttributeValues={":e": enabled, ":t": now, ":a": actor, ":d": KNOWN_FLAGS[name]},
        )
        self._refresh(force=True)
        log.info("feature_flag_changed", extra={"flag": name, "enabled": enabled, "actor": actor})
        return next(flag for flag in self.all() if flag["name"] == name)
