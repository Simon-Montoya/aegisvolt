from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from aegis_common.config import Settings
from aegis_common.logs import JsonFormatter, correlation_id_var
from billing_wallet.main import is_peak, quote


def test_quote_without_surge():
    q = quote(Decimal("10"), 50, surge_enabled=False, peak=True)
    assert q["amount"] == Decimal("11000")
    assert not q["surge_applied"]


def test_quote_surge_only_when_flag_and_peak():
    assert quote(Decimal("10"), 50, True, False)["surge_applied"] is False
    surged = quote(Decimal("10"), 50, True, True)
    assert surged["surge_applied"] and surged["amount"] == Decimal("16500")


def test_ultra_charger_rate():
    assert quote(Decimal("10"), 180, False, False)["amount"] == Decimal("13750")


def test_peak_hours_in_bogota():
    bogota = ZoneInfo("America/Bogota")
    assert is_peak(datetime(2026, 11, 19, 18, 30, tzinfo=bogota))
    assert not is_peak(datetime(2026, 11, 19, 3, 0, tzinfo=bogota))


def test_json_log_has_correlation_env_and_service():
    settings = Settings(service_name="billing-wallet-service", environment="test")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "wallet_charged", None, None)
    record.amount = "1000"
    token = correlation_id_var.set("saga-123")
    try:
        payload = json.loads(JsonFormatter(settings).format(record))
    finally:
        correlation_id_var.reset(token)
    assert payload["correlation_id"] == "saga-123"
    assert payload["environment"] == "test"
    assert payload["service"] == "billing-wallet-service"
    assert payload["amount"] == "1000"


def test_settings_reject_unknown_environment():
    with pytest.raises(ValueError):
        Settings(service_name="x", environment="staging")


def test_table_and_log_group_names_are_per_environment():
    s = Settings(service_name="billing-wallet-service", environment="prod", project="aegisvolt")
    assert s.table_name("billing") == "aegisvolt-billing-prod"
    assert s.log_group == "/aegisvolt/prod/billing-wallet-service"
