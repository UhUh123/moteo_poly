from __future__ import annotations

import json
from pathlib import Path

from detect_temperature.status import KNOWN_TASKS, load_health, update_task


def test_load_health_missing_returns_scaffold(tmp_path: Path) -> None:
    health_path = tmp_path / "health.json"
    payload = load_health(health_path)
    assert set(KNOWN_TASKS).issubset(payload["tasks"].keys())
    assert payload["portfolio"] == {}
    assert payload["alerts"] == []


def test_update_task_creates_and_merges(tmp_path: Path) -> None:
    health_path = tmp_path / "health.json"
    update_task("collector_regular", {"code": 0, "markets_scanned": 848}, path=health_path)
    update_task("collector_regular", {"code": 0, "snapshot_dir": "abc"}, path=health_path)

    raw = json.loads(health_path.read_text(encoding="utf-8"))
    section = raw["tasks"]["collector_regular"]
    assert section["code"] == 0
    assert section["markets_scanned"] == 848  # retained from first call
    assert section["snapshot_dir"] == "abc"    # added by second call
    assert "last_run" in section


def test_update_task_portfolio_and_alerts(tmp_path: Path) -> None:
    health_path = tmp_path / "health.json"
    update_task(
        "daily_open_trades",
        {"code": 0, "positions_opened": 8},
        path=health_path,
        portfolio={"bankroll_usdc": 100.0, "open_positions": 8},
        alert="drawdown safe, opened 8 positions",
    )
    payload = load_health(health_path)
    assert payload["portfolio"]["bankroll_usdc"] == 100.0
    assert len(payload["alerts"]) == 1
    assert "daily_open_trades" in payload["alerts"][0]


def test_update_task_trims_alerts(tmp_path: Path) -> None:
    health_path = tmp_path / "health.json"
    for i in range(60):
        update_task("collector_regular", {"code": 0}, path=health_path, alert=f"event {i}")
    payload = load_health(health_path)
    assert len(payload["alerts"]) == 50
    # newest first
    assert "event 59" in payload["alerts"][0]


def test_update_task_atomic_replace(tmp_path: Path, monkeypatch) -> None:
    health_path = tmp_path / "health.json"
    update_task("collector_regular", {"code": 0}, path=health_path)
    # Ensure .tmp and .lock did not persist
    assert not (tmp_path / "health.json.tmp").exists()
    assert not (tmp_path / "health.json.lock").exists()


# ---- error-code semantics --------------------------------------------------
#
# These tests pin the rule that a failing task run replaces (not merges) the
# task section. Without it `update_task` lets stale success-side fields like
# `outcome=snapshot` or `snapshot_dir=...` leak into a code=2 record, which
# is what made health.json LIE during the 2026-05-18 DNS outage:
# collector_metar showed code=2 AND outcome=snapshot at the same time.


def test_update_task_error_clears_prior_success_fields(tmp_path: Path) -> None:
    health_path = tmp_path / "health.json"
    update_task(
        "collector_metar",
        {
            "code": 0,
            "stations_requested": 51,
            "reports_received": 50,
            "rows_appended": 27,
            "outcome": "snapshot",
            "days_touched": ["2026-05-17"],
        },
        path=health_path,
    )
    update_task(
        "collector_metar",
        {"code": 2, "error": "DNS resolution failed"},
        path=health_path,
    )

    section = load_health(health_path)["tasks"]["collector_metar"]
    assert section["code"] == 2
    assert section["error"] == "DNS resolution failed"
    # All success-side fields must be gone:
    for stale_key in ("outcome", "stations_requested", "reports_received",
                      "rows_appended", "days_touched"):
        assert stale_key not in section, (
            f"{stale_key!r} leaked into the failed-state task section "
            "and would make health.json claim success+failure at the same time"
        )
    assert "last_run" in section


def test_update_task_success_after_error_starts_fresh(tmp_path: Path) -> None:
    """Recovery path: after an error wipes the section, the next success
    must rebuild it cleanly. This means the merge-on-success branch
    really does start from {} when prior was an error."""
    health_path = tmp_path / "health.json"
    update_task("collector_metar", {"code": 2, "error": "boom"}, path=health_path)
    update_task(
        "collector_metar",
        {"code": 0, "outcome": "snapshot", "rows_appended": 12},
        path=health_path,
    )

    section = load_health(health_path)["tasks"]["collector_metar"]
    assert section["code"] == 0
    assert section["outcome"] == "snapshot"
    assert section["rows_appended"] == 12
    assert "error" not in section, "error field from prior run must not survive a fresh success"


def test_update_task_consecutive_errors_replace_each_time(tmp_path: Path) -> None:
    """Two failed runs in a row: the second one's fields fully replace the
    first one's. We must not accumulate stale error context either."""
    health_path = tmp_path / "health.json"
    update_task(
        "collector_metar",
        {"code": 2, "error": "DNS failed",
         "attempt": 1, "diagnostic": "first failure detail"},
        path=health_path,
    )
    update_task(
        "collector_metar",
        {"code": 3, "error": "different problem"},
        path=health_path,
    )

    section = load_health(health_path)["tasks"]["collector_metar"]
    assert section["code"] == 3
    assert section["error"] == "different problem"
    # First failure's diagnostic context must not survive
    assert "attempt" not in section
    assert "diagnostic" not in section


def test_update_task_treats_non_zero_string_codes_as_errors(tmp_path: Path) -> None:
    """Some callers pass code as int, some PowerShell paths might pass a
    string. Make sure '2' (str) behaves like 2 (int)."""
    health_path = tmp_path / "health.json"
    update_task("collector_regular", {"code": 0, "outcome": "snapshot"}, path=health_path)
    update_task("collector_regular", {"code": "2", "error": "oops"}, path=health_path)

    section = load_health(health_path)["tasks"]["collector_regular"]
    assert section["code"] == "2"
    assert "outcome" not in section


def test_update_task_zero_code_passes_through_normally(tmp_path: Path) -> None:
    """code=0 must keep merging fields — otherwise a successful run
    that only updates a single field would erase everything else."""
    health_path = tmp_path / "health.json"
    update_task(
        "collector_regular",
        {"code": 0, "markets_scanned": 475, "snapshot_dir": "abc"},
        path=health_path,
    )
    update_task(
        "collector_regular",
        {"code": 0, "snapshot_dir": "xyz"},  # only snapshot_dir changes
        path=health_path,
    )

    section = load_health(health_path)["tasks"]["collector_regular"]
    assert section["snapshot_dir"] == "xyz"
    assert section["markets_scanned"] == 475, "earlier success-side fields must be retained"


def test_update_task_alert_still_recorded_during_error(tmp_path: Path) -> None:
    """Alerts live at the top level, not inside the task section. They
    must continue to be recorded even when the task section is replaced."""
    health_path = tmp_path / "health.json"
    update_task("collector_metar", {"code": 0, "outcome": "snapshot"}, path=health_path)
    update_task(
        "collector_metar",
        {"code": 2, "error": "DNS failed"},
        path=health_path,
        alert="metar fetch failed: DNS",
    )

    payload = load_health(health_path)
    assert any("metar fetch failed" in a for a in payload["alerts"])
    assert payload["tasks"]["collector_metar"]["code"] == 2
