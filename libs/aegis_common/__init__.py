"""Librería compartida de AegisVolt (config, AWS/Floci, logs JSON, feature flags, web)."""

from datetime import UTC, datetime
from decimal import Decimal


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def to_jsonable(value):
    """DynamoDB devuelve Decimal; se convierte a int/float para JSON."""
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value
