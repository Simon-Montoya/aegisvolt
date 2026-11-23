"""Clientes boto3 apuntando a Floci (o a AWS real si AWS_ENDPOINT_URL no está definido)."""

from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.config import Config

from .config import Settings

_BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=3, read_timeout=5)


def _kwargs(settings: Settings) -> dict:
    kwargs: dict = {"region_name": settings.aws_region, "config": _BOTO_CONFIG}
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    return kwargs


@lru_cache(maxsize=32)
def _resource(name: str, region: str, endpoint: str | None):
    kwargs = {"region_name": region, "config": _BOTO_CONFIG}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.resource(name, **kwargs)


def dynamodb(settings: Settings):
    return _resource("dynamodb", settings.aws_region, settings.aws_endpoint_url)


def table(settings: Settings, kind: str):
    return dynamodb(settings).Table(settings.table_name(kind))


def client(settings: Settings, name: str):
    return boto3.client(name, **_kwargs(settings))


def reset_cache() -> None:
    """Las pruebas con moto necesitan recrear los recursos dentro del mock."""
    _resource.cache_clear()
