from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from detect_temperature.markets import normalize_market
from detect_temperature.pipeline import collect_actuals
from detect_temperature.sources.actuals import (
    ActualTemperature,
    _extract_synoptic_temperatures,
    _is_finalized_enough,
    error_actual_for_target,
)


def test_extract_synoptic_temperatures_from_primary_key() -> None:
    assert _extract_synoptic_temperatures({"air_temp_set_1": [12.1, None, "13.4", "M"]}) == [12.1, 13.4]


def test_finalization_lag_skips_today() -> None:
    assert not _is_finalized_enough(date(2026, 5, 4), today=date(2026, 5, 4), lag_days=1)
    assert _is_finalized_enough(date(2026, 5, 3), today=date(2026, 5, 4), lag_days=1)


def test_error_actual_preserves_target_identity() -> None:
    target = normalize_market(
        {
            "title": "Highest temperature in Houston on May 5?",
            "slug": "highest-temperature-in-houston-on-may-5-2026",
            "location": "William P. Hobby",
            "resolution_source_url": "https://www.wunderground.com/history/daily/us/tx/houston/KHOU",
            "description": "recorded at the William P. Hobby Airport Station in degrees Fahrenheit on 5 May '26.",
        }
    )

    actual = error_actual_for_target(target, "boom")

    assert actual.slug == target.slug
    assert actual.status == "error"
    assert actual.notes == "boom"



def _write_targets(path: Path, slugs: list[str]) -> None:
    fields = [
        "title", "slug", "city", "location_name", "target_date",
        "target_extreme", "target_unit", "station_id",
        "resolution_source_url", "source_domain", "description",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for slug in slugs:
            w.writerow({
                "title": slug, "slug": slug, "city": "X", "location_name": "",
                "target_date": "2026-05-11", "target_extreme": "max",
                "target_unit": "celsius", "station_id": "KSFO",
                "resolution_source_url": "https://www.wunderground.com/weather/KSFO",
                "source_domain": "wunderground.com", "description": "",
            })


def test_collect_actuals_merges_instead_of_overwriting(tmp_path, monkeypatch) -> None:
    """Rotating targets.csv day-over-day must not wipe resolved ok rows."""
    actuals_path = tmp_path / "actuals.csv"
    old_fields = [
        "slug", "station_id", "target_date", "target_extreme",
        "resolution_unit", "observed_temp_c", "observed_temp_f",
        "observed_resolution_value", "provider", "status", "sample_count",
        "source_url", "notes",
    ]
    with actuals_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=old_fields)
        w.writeheader()
        w.writerow({
            "slug": "yesterday-sf",
            "station_id": "KSFO", "target_date": "2026-05-10", "target_extreme": "max",
            "resolution_unit": "fahrenheit", "observed_temp_c": "20.0",
            "observed_temp_f": "68.0", "observed_resolution_value": "68",
            "provider": "weather_com_historical", "status": "ok",
            "sample_count": "12", "source_url": "x", "notes": "",
        })

    targets_path = tmp_path / "targets.csv"
    _write_targets(targets_path, ["today-ny"])

    def fake_collect(target, station, finalization_lag_days=1):
        return ActualTemperature(
            slug=target.slug, station_id=target.station_id,
            target_date=target.target_date, target_extreme=target.target_extreme,
            resolution_unit=target.target_unit,
            observed_temp_c=None, observed_temp_f=None, observed_resolution_value=None,
            provider="none", status="pending", sample_count=0, source_url="", notes="waiting",
        )
    monkeypatch.setattr("detect_temperature.pipeline.collect_actual_for_target", fake_collect)

    result = collect_actuals(
        targets_path=targets_path, output_path=actuals_path,
        station_catalog=None, finalization_lag_days=1,
    )

    slugs = {r["slug"] for r in result}
    assert "yesterday-sf" in slugs
    assert "today-ny" in slugs
    on_disk = list(csv.DictReader(actuals_path.open(newline="", encoding="utf-8")))
    yest = next(r for r in on_disk if r["slug"] == "yesterday-sf")
    assert yest["status"] == "ok"


def test_collect_actuals_promotes_pending_to_ok(tmp_path, monkeypatch) -> None:
    actuals_path = tmp_path / "actuals.csv"
    with actuals_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["slug", "status", "observed_temp_c"])
        w.writeheader()
        w.writerow({"slug": "market-a", "status": "pending", "observed_temp_c": ""})

    targets_path = tmp_path / "targets.csv"
    _write_targets(targets_path, ["market-a"])

    def fake_collect(target, station, finalization_lag_days=1):
        return ActualTemperature(
            slug=target.slug, station_id=target.station_id,
            target_date=target.target_date, target_extreme=target.target_extreme,
            resolution_unit="celsius",
            observed_temp_c=22.5, observed_temp_f=72.5, observed_resolution_value=22.5,
            provider="test", status="ok", sample_count=24, source_url="x",
        )
    monkeypatch.setattr("detect_temperature.pipeline.collect_actual_for_target", fake_collect)

    collect_actuals(
        targets_path=targets_path, output_path=actuals_path,
        station_catalog=None, finalization_lag_days=1,
    )
    on_disk = list(csv.DictReader(actuals_path.open(newline="", encoding="utf-8")))
    assert len(on_disk) == 1 and on_disk[0]["status"] == "ok"


def test_collect_actuals_does_not_downgrade_ok_to_pending(tmp_path, monkeypatch) -> None:
    actuals_path = tmp_path / "actuals.csv"
    with actuals_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["slug", "status", "observed_temp_c"])
        w.writeheader()
        w.writerow({"slug": "market-b", "status": "ok", "observed_temp_c": "15.0"})

    targets_path = tmp_path / "targets.csv"
    _write_targets(targets_path, ["market-b"])

    def fake_collect(target, station, finalization_lag_days=1):
        return ActualTemperature(
            slug=target.slug, station_id=target.station_id,
            target_date=target.target_date, target_extreme=target.target_extreme,
            resolution_unit="celsius",
            observed_temp_c=None, observed_temp_f=None, observed_resolution_value=None,
            provider="none", status="pending", sample_count=0, source_url="",
            notes="api flake",
        )
    monkeypatch.setattr("detect_temperature.pipeline.collect_actual_for_target", fake_collect)

    collect_actuals(
        targets_path=targets_path, output_path=actuals_path,
        station_catalog=None, finalization_lag_days=1,
    )
    on_disk = list(csv.DictReader(actuals_path.open(newline="", encoding="utf-8")))
    assert len(on_disk) == 1 and on_disk[0]["status"] == "ok" and on_disk[0]["observed_temp_c"] == "15.0"


def test_collect_actuals_recovers_stuck_open_paper_positions(tmp_path, monkeypatch) -> None:
    """If a paper position is still open but its slug rotated out of
    targets.csv, collect_actuals must still try to fetch its actual.

    Pre-fix behaviour: stuck-open positions for May 11-14 sat in actuals
    as 'pending' forever because their slug was no longer in the daily
    targets.csv and there was no fallback. Now we recover the missing
    station_id from artifacts/paper_runs/*/targets.csv archives and
    queue the slug.
    """
    actuals_path = tmp_path / "actuals.csv"
    actuals_path.write_text("slug,status,observed_temp_c\n", encoding="utf-8")

    # Today's targets.csv has only tomorrow's slug.
    targets_path = tmp_path / "data" / "targets.csv"
    targets_path.parent.mkdir(parents=True)
    _write_targets(targets_path, ["highest-temperature-in-tokyo-on-may-20-2026"])

    # Yesterday's archive holds the slug we still have an open position on.
    archive_root = tmp_path / "artifacts" / "paper_runs" / "20260514T190002Z-pre-open"
    archive_root.mkdir(parents=True)
    _write_targets(archive_root / "targets.csv",
                   ["highest-temperature-in-shanghai-on-may-12-2026"])

    # Paper portfolio: one stuck-open position whose slug is in the archive
    # but not in current targets.csv.
    portfolio_path = tmp_path / "artifacts" / "paper_portfolio.csv"
    with portfolio_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["status", "event_slug", "interval_unit"])
        w.writeheader()
        w.writerow({
            "status": "open",
            "event_slug": "highest-temperature-in-shanghai-on-may-12-2026",
            "interval_unit": "celsius",
        })

    fetched_slugs: list[str] = []

    def fake_collect(target, station, finalization_lag_days=1):
        fetched_slugs.append(target.slug)
        return ActualTemperature(
            slug=target.slug, station_id=target.station_id,
            target_date=target.target_date, target_extreme=target.target_extreme,
            resolution_unit="celsius",
            observed_temp_c=29.7, observed_temp_f=85.5, observed_resolution_value=29.7,
            provider="test", status="ok", sample_count=24, source_url="x",
        )

    monkeypatch.setattr("detect_temperature.pipeline.collect_actual_for_target", fake_collect)

    result = collect_actuals(
        targets_path=targets_path,
        output_path=actuals_path,
        station_catalog=None,
        finalization_lag_days=1,
        portfolio_path=portfolio_path,
        paper_runs_root=tmp_path / "artifacts" / "paper_runs",
    )

    fetched = set(fetched_slugs)
    assert "highest-temperature-in-tokyo-on-may-20-2026" in fetched, "today's slug must still be queued"
    assert "highest-temperature-in-shanghai-on-may-12-2026" in fetched, "stuck-open slug must be recovered"
    statuses = {r["slug"]: r["status"] for r in result}
    assert statuses["highest-temperature-in-shanghai-on-may-12-2026"] == "ok"


def test_collect_actuals_skips_already_settled_paper_rows(tmp_path, monkeypatch) -> None:
    """Settled positions (status=won/lost) must NOT be re-queued by the
    stuck-open recovery path. Only open / at_risk / pending_actual.
    """
    actuals_path = tmp_path / "actuals.csv"
    targets_path = tmp_path / "data" / "targets.csv"
    targets_path.parent.mkdir(parents=True)
    _write_targets(targets_path, [])  # empty current targets

    archive_root = tmp_path / "artifacts" / "paper_runs" / "20260514T190002Z-pre-open"
    archive_root.mkdir(parents=True)
    _write_targets(archive_root / "targets.csv",
                   ["highest-temperature-in-london-on-may-12-2026",
                    "highest-temperature-in-paris-on-may-12-2026"])

    portfolio_path = tmp_path / "artifacts" / "paper_portfolio.csv"
    with portfolio_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["status", "event_slug", "interval_unit"])
        w.writeheader()
        w.writerow({"status": "won", "event_slug": "highest-temperature-in-london-on-may-12-2026", "interval_unit": "celsius"})
        w.writerow({"status": "open", "event_slug": "highest-temperature-in-paris-on-may-12-2026", "interval_unit": "celsius"})

    fetched: list[str] = []
    def fake_collect(target, station, finalization_lag_days=1):
        fetched.append(target.slug)
        return ActualTemperature(
            slug=target.slug, station_id=target.station_id,
            target_date=target.target_date, target_extreme=target.target_extreme,
            resolution_unit="celsius", observed_temp_c=20.0, observed_temp_f=68.0,
            observed_resolution_value=20.0, provider="test", status="ok",
            sample_count=1, source_url="",
        )
    monkeypatch.setattr("detect_temperature.pipeline.collect_actual_for_target", fake_collect)

    collect_actuals(
        targets_path=targets_path,
        output_path=actuals_path,
        station_catalog=None,
        finalization_lag_days=1,
        portfolio_path=portfolio_path,
        paper_runs_root=tmp_path / "artifacts" / "paper_runs",
    )

    assert "highest-temperature-in-paris-on-may-12-2026" in fetched, "open position should be retried"
    assert "highest-temperature-in-london-on-may-12-2026" not in fetched, "settled position must not be retried"
