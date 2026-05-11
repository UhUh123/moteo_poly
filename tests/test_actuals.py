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
