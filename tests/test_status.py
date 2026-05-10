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
