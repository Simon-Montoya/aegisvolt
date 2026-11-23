"""Clientes HTTP hacia los tres microservicios (vía Services de Kubernetes).

Cada llamada propaga X-Correlation-ID = saga_id para que los logs de los tres
servicios queden enlazados en CloudWatch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from aegis_common.logs import correlation_id_var
from aegis_common.web import CORRELATION_HEADER


class BusinessFailure(Exception):
    """El servicio respondió con un rechazo de negocio definitivo (4xx/502 con código)."""

    def __init__(self, status_code: int, code: str, message: str, body: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.body = body or {}


class TechnicalFailure(Exception):
    """Timeout, conexión rechazada o 5xx sin código: el resultado remoto es INCIERTO."""


@dataclass
class ServiceUrls:
    reservation: str = field(default_factory=lambda: os.getenv("STATION_RESERVATION_URL", "http://station-reservation-service"))
    billing: str = field(default_factory=lambda: os.getenv("BILLING_WALLET_URL", "http://billing-wallet-service"))
    dispatch: str = field(default_factory=lambda: os.getenv("ENERGY_DISPATCH_URL", "http://energy-dispatch-service"))


class ServiceClients:
    def __init__(self, urls: ServiceUrls | None = None, timeout: float = 5.0, transport: httpx.BaseTransport | None = None) -> None:
        self.urls = urls or ServiceUrls()
        self.http = httpx.Client(timeout=timeout, transport=transport)

    # ---- utilidades -------------------------------------------------------
    def _headers(self, correlation_id: str | None = None) -> dict:
        cid = correlation_id or correlation_id_var.get()
        return {CORRELATION_HEADER: cid} if cid else {}

    def _call(self, method: str, url: str, correlation_id: str | None = None, **kwargs) -> dict | list:
        try:
            response = self.http.request(method, url, headers=self._headers(correlation_id), **kwargs)
        except httpx.HTTPError as exc:
            raise TechnicalFailure(f"{method} {url}: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code < 400:
            return response.json()
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        if code:
            raise BusinessFailure(response.status_code, code, body.get("message", ""), body)
        raise TechnicalFailure(f"{method} {url}: HTTP {response.status_code}")

    # ---- paso 1 · reservas -----------------------------------------------
    def reserve(self, saga_id: str, connector_id: str, fleet_id: str, fast_charge_boost: bool) -> dict:
        return self._call("POST", f"{self.urls.reservation}/reservations", saga_id, json={"saga_id": saga_id, "connector_id": connector_id, "fleet_id": fleet_id, "fast_charge_boost": fast_charge_boost})

    def release(self, saga_id: str, reason: str) -> dict:
        return self._call("POST", f"{self.urls.reservation}/reservations/{saga_id}/release", saga_id, params={"reason": reason})

    def confirm(self, saga_id: str) -> dict:
        return self._call("POST", f"{self.urls.reservation}/reservations/{saga_id}/confirm", saga_id)

    def stations(self) -> list:
        return self._call("GET", f"{self.urls.reservation}/stations")

    def finish(self, connector_id: str) -> dict:
        return self._call("POST", f"{self.urls.reservation}/connectors/{connector_id}/finish")

    # ---- paso 2 · billetera ----------------------------------------------
    def charge(self, saga_id: str, fleet_id: str, energy_kwh: float, power_kw: int) -> dict:
        return self._call("POST", f"{self.urls.billing}/charges", saga_id, json={"saga_id": saga_id, "fleet_id": fleet_id, "energy_kwh": str(energy_kwh), "power_kw": power_kw})

    def refund(self, saga_id: str, reason: str) -> dict:
        return self._call("POST", f"{self.urls.billing}/charges/{saga_id}/refund", saga_id, params={"reason": reason})

    def wallet(self, fleet_id: str) -> dict:
        return self._call("GET", f"{self.urls.billing}/wallets/{fleet_id}")

    # ---- paso 3 · despacho de energía ------------------------------------
    def start_session(self, saga_id: str, connector_id: str, energy_kwh: float, power_kw: int) -> dict:
        return self._call("POST", f"{self.urls.dispatch}/sessions", saga_id, json={"saga_id": saga_id, "connector_id": connector_id, "energy_kwh": str(energy_kwh), "power_kw": power_kw})

    def telemetry(self, connector_id: str, limit: int = 20) -> list:
        return self._call("GET", f"{self.urls.dispatch}/telemetry/{connector_id}", params={"limit": limit})

    def health(self) -> dict:
        result = {}
        for name, base in (("station-reservation-service", self.urls.reservation), ("billing-wallet-service", self.urls.billing), ("energy-dispatch-service", self.urls.dispatch)):
            try:
                result[name] = self._call("GET", f"{base}/health").get("status", "unknown")
            except (TechnicalFailure, BusinessFailure) as exc:
                result[name] = f"down: {exc}"
        return result
