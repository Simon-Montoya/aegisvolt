"""Configuración compartida por todos los microservicios de AegisVolt.

Todo se lee de variables de entorno para que la MISMA imagen Docker se
promueva sin cambios entre dev -> test -> prod; lo único que cambia es el
ConfigMap de Kubernetes de cada ambiente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

VALID_ENVIRONMENTS = ("dev", "test", "prod")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    service_name: str
    environment: str = field(default_factory=lambda: os.getenv("AEGIS_ENV", "dev"))
    project: str = field(default_factory=lambda: os.getenv("AEGIS_PROJECT", "aegisvolt"))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")))
    # Floci expone todos los servicios AWS en un único endpoint (http://localhost:4566).
    aws_endpoint_url: str | None = field(default_factory=lambda: os.getenv("AWS_ENDPOINT_URL") or None)
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "local"))
    # stable | canary: lo usa el análisis del Canary Deployment.
    deploy_track: str = field(default_factory=lambda: os.getenv("DEPLOY_TRACK", "stable"))
    cloudwatch_logs: bool = field(default_factory=lambda: _bool(os.getenv("CLOUDWATCH_LOGS"), False))
    flags_cache_seconds: float = field(default_factory=lambda: float(os.getenv("FLAGS_CACHE_SECONDS", "3")))

    def __post_init__(self) -> None:
        if self.environment not in VALID_ENVIRONMENTS:
            raise ValueError(f"AEGIS_ENV debe ser uno de {VALID_ENVIRONMENTS}, llegó '{self.environment}'")

    def table_name(self, kind: str) -> str:
        """aegisvolt-<kind>-<env>, p. ej. aegisvolt-reservations-dev (igual que en Terraform)."""
        override = os.getenv(f"TABLE_{kind.upper().replace('-', '_')}")
        return override or f"{self.project}-{kind}-{self.environment}"

    @property
    def log_group(self) -> str:
        """/aegisvolt/<env>/<service> (igual que en Terraform)."""
        return f"/{self.project}/{self.environment}/{self.service_name}"
