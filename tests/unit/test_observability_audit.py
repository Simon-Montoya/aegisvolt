from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "observability" / "prefect"))

from audit import audit  # noqa: E402

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def saga(sid, status, minutes_ago=1):
    return {"saga_id": sid, "status": status, "updated_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


STATE = {
    "ok-completed": ({"status": "CONFIRMED"}, {"status": "CHARGED"}, {"status": "ACTIVE"}),
    "ok-compensated": ({"status": "RELEASED"}, {"status": "REFUNDED"}, {"status": "FAILED"}),
    "bad-compensated": ({"status": "RESERVED"}, {"status": "CHARGED"}, {"status": "FAILED"}),
    "orphan": ({"status": "RESERVED"}, None, None),
}


def test_audit_classifies_orphans_and_inconsistencies():
    sagas = [saga("ok-completed", "COMPLETED"), saga("ok-compensated", "COMPENSATED"), saga("bad-compensated", "COMPENSATED"), saga("orphan", "RESERVED", minutes_ago=30)]
    result = audit("test", sagas, lambda sid: STATE[sid], now=NOW)
    assert result.total == 4
    assert [o["saga_id"] for o in result.orphans] == ["orphan"]
    assert [i["saga_id"] for i in result.inconsistencies] == ["bad-compensated"]
    assert len(result.compensated) == 2
    assert not result.healthy


def test_running_saga_is_not_orphan_yet():
    result = audit("dev", [saga("orphan", "CHARGED", minutes_ago=1)], lambda sid: STATE[sid], now=NOW)
    assert result.orphans == [] and result.healthy
