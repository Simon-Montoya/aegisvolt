"""Configuración común de pytest.

Backend de AWS para las pruebas:
  * Por defecto: moto (en proceso, sin Docker).
  * Si FLOCI_ENDPOINT está definido (p. ej. el servicio floci del pipeline de
    GitLab), las pruebas de integración y e2e corren contra Floci real.

Las tablas de prueba usan el prefijo 'aegisci' para no tocar nunca las tablas
aegisvolt-*-<env> de los ambientes reales aunque se apunte al Floci local.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in [ROOT / "libs", *sorted((ROOT / "services").iterdir())]:
    sys.path.insert(0, str(path))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["AWS_REGION"] = os.environ["AWS_DEFAULT_REGION"]
os.environ["AEGIS_ENV"] = "test"
os.environ["AEGIS_PROJECT"] = "aegisci"
os.environ["FLAGS_CACHE_SECONDS"] = "0"
os.environ["CLOUDWATCH_LOGS"] = "false"
os.environ.pop("SURGE_FORCE_PEAK", None)

FLOCI_ENDPOINT = os.getenv("FLOCI_ENDPOINT")
if FLOCI_ENDPOINT:
    os.environ["AWS_ENDPOINT_URL"] = FLOCI_ENDPOINT
else:
    os.environ.pop("AWS_ENDPOINT_URL", None)


@pytest.fixture(scope="session")
def aws_session():
    if FLOCI_ENDPOINT:
        yield "floci"
        return
    from moto import mock_aws

    with mock_aws():
        yield "moto"


@pytest.fixture()
def aws_env(aws_session):
    """Tablas limpias y datos semilla para cada prueba."""
    from aegis_common import aws
    from aegis_common.config import Settings
    from aegis_common.seed import seed
    from aegis_common.tables import create_all, delete_all

    aws.reset_cache()
    settings = Settings(service_name="tests")
    dynamodb = aws.dynamodb(settings)
    delete_all(dynamodb, settings)
    if FLOCI_ENDPOINT:
        for kind in ("reservations", "billing", "dispatch", "sagas", "feature-flags"):
            try:
                dynamodb.meta.client.get_waiter("table_not_exists").wait(TableName=settings.table_name(kind), WaiterConfig={"Delay": 1, "MaxAttempts": 20})
            except Exception:  # noqa: BLE001
                pass
    create_all(dynamodb, settings)
    seed(dynamodb, settings)
    yield {"settings": settings, "dynamodb": dynamodb, "backend": aws_session}
