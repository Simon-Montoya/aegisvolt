"""Dependencias del gateway (singletons perezosos, reemplazables en las pruebas)."""

from __future__ import annotations

from aegis_common import aws
from aegis_common.config import Settings
from aegis_common.flags import FeatureFlags

from .clients import ServiceClients
from .saga import SagaOrchestrator, SagaStore

settings = Settings(service_name="graphql-gateway")

_clients: ServiceClients | None = None
_flags: FeatureFlags | None = None
_orchestrator: SagaOrchestrator | None = None


def clients() -> ServiceClients:
    global _clients
    if _clients is None:
        _clients = ServiceClients()
    return _clients


def flags() -> FeatureFlags:
    global _flags
    if _flags is None:
        _flags = FeatureFlags(settings)
    return _flags


def store() -> SagaStore:
    return SagaStore(aws.table(settings, "sagas"))


def orchestrator() -> SagaOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SagaOrchestrator(store(), clients(), settings.environment)
    return _orchestrator


def reset(new_clients: ServiceClients | None = None) -> None:
    global _clients, _flags, _orchestrator
    _clients, _flags, _orchestrator = new_clients, None, None
