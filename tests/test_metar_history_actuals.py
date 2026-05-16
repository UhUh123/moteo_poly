"""Tests for MetarHistoryActualsProvider and the actuals fallback path.

The fallback exists because `weather.com` returns 403 for non-US ICAO
codes (RKSI, ZSPD, LLBG, WSSS, ...) under our public apiKey, leaving
~44 paper positions per day stuck in `error`. The METAR archive that
`metar_collector` already persists carries the same data, just unaggre-
gated. These tests pin the contract so the fallback can never silently
return false data.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from detect_temperature.schema import MarketTarget
from detect_temperature.sources.actuals import (
    METAR_MIN_SAMPLES_FOR_DAILY_EXTREME,
    MetarHistoryActualsProvider,
    _local_day_utc_window,
    _station_local_offset_hours,
    collect_actual_for_target,
)
from detect_temperature.sources.base import StationMetadata


METAR_COLUMNS = [
    "station_id",
    "observed_at",
    "report_type",
    "temp_c",
    "dewpoint_c",
    "wind_dir_deg",
    "wind_speed_kt",
    "altim_hpa",
    "visibility",
    "raw_text",
    "fetched_at",
]


def _write_metar_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=METAR_COLUMNS)
        writer.writeheader()
        for row in rows:
            full = {col: "" for col in METAR_COLUMNS}
            full.update(row)
            writer.writerow(full)


def _make_target(
    *,
    slug: str = "highest-temperature-in-tokyo-on-may-12-2026",
    target_date: date = date(2026, 5, 12),
    target_extreme: str = "max",
    target_unit: str = "celsius",
    station_id: str = "RJAA",
    source_domain: str = "wunderground.com",
) -> MarketTarget:
    return MarketTarget(
        title=slug,
        slug=slug,
        city="Tokyo",
        location_name="",
        target_date=target_date,
        target_extreme=target_extreme,
        target_unit=target_unit,
        station_id=station_id,
        resolution_source_url=f"https://www.wunderground.com/weather/{station_id}",
        source_domain=source_domain,
        description="",
    )


def _hourly_rows(
    station_id: str,
    iso_day: str,
    temps_by_hour: dict[int, float],
) -> list[dict]:
    return [
        {
            "station_id": station_id,
            "observed_at": f"{iso_day}T{hour:02d}:00:00+00:00",
            "report_type": "METAR",
            "temp_c": str(temp),
            "fetched_at": f"{iso_day}T{hour:02d}:05:00+00:00",
        }
        for hour, temp in temps_by_hour.items()
    ]


def test_local_day_utc_window_centred_on_utc_for_zero_offset() -> None:
    start, end = _local_day_utc_window(date(2026, 5, 12), 0.0)
    assert start.isoformat() == "2026-05-12T00:00:00+00:00"
    assert end.isoformat() == "2026-05-13T00:00:00+00:00"


def test_local_day_utc_window_shifts_for_eastern_offset() -> None:
    """Shanghai (UTC+8): May 12 local = May 11 16:00 UTC .. May 12 16:00 UTC."""
    start, end = _local_day_utc_window(date(2026, 5, 12), 8.0)
    assert start.isoformat() == "2026-05-11T16:00:00+00:00"
    assert end.isoformat() == "2026-05-12T16:00:00+00:00"


def test_local_day_utc_window_shifts_for_western_offset() -> None:
    """Honolulu (UTC-10): May 12 local = May 12 10:00 UTC .. May 13 10:00 UTC."""
    start, end = _local_day_utc_window(date(2026, 5, 12), -10.0)
    assert start.isoformat() == "2026-05-12T10:00:00+00:00"
    assert end.isoformat() == "2026-05-13T10:00:00+00:00"


def test_local_day_utc_window_rejects_absurd_offset() -> None:
    assert _local_day_utc_window(date(2026, 5, 12), 50.0) is None


def test_station_local_offset_falls_back_to_zero_without_longitude() -> None:
    assert _station_local_offset_hours(None) == 0.0
    assert _station_local_offset_hours(StationMetadata(station_id="X")) == 0.0


def test_station_local_offset_uses_longitude_proxy() -> None:
    east = StationMetadata(station_id="ZSPD", longitude=121.8)
    assert pytest.approx(_station_local_offset_hours(east), abs=0.01) == 121.8 / 15.0


def test_metar_provider_returns_max_for_local_day(tmp_path) -> None:
    """For Tokyo (UTC+9) on May 12 local, the window crosses May 11 15:00 UTC
    .. May 12 15:00 UTC. The provider must aggregate across BOTH UTC files
    and pick the highest temperature inside the local-day window."""
    history = tmp_path / "metar_history"
    # May 11 UTC: only the last hours fall inside Tokyo's May 12 local day.
    _write_metar_csv(
        history / "2026-05-11.csv",
        _hourly_rows("RJAA", "2026-05-11", {16: 18.0, 18: 19.5, 22: 21.0}),
    )
    # May 12 UTC: hours up to 15:00 fall inside Tokyo's May 12 local day.
    _write_metar_csv(
        history / "2026-05-12.csv",
        _hourly_rows(
            "RJAA",
            "2026-05-12",
            {0: 22.0, 3: 24.0, 6: 28.5, 9: 26.0, 12: 24.0, 14: 23.0, 18: 99.0},
        ),
    )
    target = _make_target(station_id="RJAA")
    station = StationMetadata(station_id="RJAA", longitude=140.4)  # ~UTC+9.36

    actual = MetarHistoryActualsProvider(
        history_root=history, min_samples=3
    ).collect(target, station)

    assert actual.status == "ok"
    assert actual.provider == "metar_history_archive"
    # 28.5 is the daily max inside [May 11 15:00 UTC, May 12 15:00 UTC)
    # The 99.0 outlier at May 12 18:00 UTC must NOT bleed in (next day).
    assert actual.observed_temp_c == 28.5
    assert actual.observed_resolution_value == 28.5
    assert actual.sample_count == 9


def test_metar_provider_returns_min_for_min_extreme(tmp_path) -> None:
    history = tmp_path / "metar_history"
    _write_metar_csv(
        history / "2026-05-12.csv",
        _hourly_rows(
            "KSFO",
            "2026-05-12",
            {hour: 12.0 + hour * 0.1 for hour in range(0, 24)},
        ),
    )
    target = _make_target(
        slug="lowest-temperature-in-san-francisco-on-may-12-2026",
        target_extreme="min",
        station_id="KSFO",
    )
    station = StationMetadata(station_id="KSFO", longitude=-120.0)  # exact UTC-8

    # KSFO May 12 local = May 12 08:00 UTC .. May 13 08:00 UTC; only May 12
    # file has data, so the min from hours 8..23 = 12.0 + 8 * 0.1 = 12.8.
    actual = MetarHistoryActualsProvider(
        history_root=history, min_samples=3
    ).collect(target, station)

    assert actual.status == "ok"
    assert pytest.approx(actual.observed_temp_c, abs=1e-9) == 12.8


def test_metar_provider_pending_when_history_root_missing(tmp_path) -> None:
    target = _make_target()
    station = StationMetadata(station_id="RJAA", longitude=140.4)

    actual = MetarHistoryActualsProvider(
        history_root=tmp_path / "does-not-exist"
    ).collect(target, station)

    assert actual.status == "pending"
    assert actual.observed_temp_c is None
    assert "metar history root missing" in (actual.notes or "")


def test_metar_provider_pending_when_no_samples(tmp_path) -> None:
    history = tmp_path / "metar_history"
    history.mkdir()  # empty dir is enough — no per-day CSVs

    actual = MetarHistoryActualsProvider(history_root=history).collect(
        _make_target(), StationMetadata(station_id="RJAA", longitude=140.4)
    )
    assert actual.status == "pending"
    assert "no metar samples" in (actual.notes or "")


def test_metar_provider_pending_below_min_samples(tmp_path) -> None:
    history = tmp_path / "metar_history"
    _write_metar_csv(
        history / "2026-05-12.csv",
        _hourly_rows("RJAA", "2026-05-12", {3: 22.0, 6: 24.0}),
    )
    actual = MetarHistoryActualsProvider(
        history_root=history, min_samples=5
    ).collect(
        _make_target(station_id="RJAA"),
        StationMetadata(station_id="RJAA", longitude=140.4),
    )
    assert actual.status == "pending"
    assert "<5" in (actual.notes or "")
    assert actual.observed_temp_c is None


def test_metar_provider_dedupes_repeated_observed_at(tmp_path) -> None:
    """Two CSVs with the same (station, observed_at) must count once."""
    history = tmp_path / "metar_history"
    base = _hourly_rows(
        "RJAA",
        "2026-05-12",
        {hour: 20.0 for hour in range(0, 14)},  # 14 unique hours
    )
    _write_metar_csv(history / "2026-05-12.csv", base)
    _write_metar_csv(history / "2026-05-13.csv", base[:5])  # 5 dupes carried over

    actual = MetarHistoryActualsProvider(
        history_root=history, min_samples=10
    ).collect(
        _make_target(station_id="RJAA"),
        StationMetadata(station_id="RJAA", longitude=0.0),  # UTC offset 0 -> May 12 UTC
    )
    assert actual.status == "ok"
    assert actual.sample_count == 14, "duplicate (station,observed_at) must collapse"


def test_metar_provider_filters_other_stations(tmp_path) -> None:
    history = tmp_path / "metar_history"
    rows = []
    for sid in ("RJAA", "RJTT"):
        rows.extend(
            _hourly_rows(sid, "2026-05-12", {hour: 20.0 + hour * 0.1 for hour in range(0, 14)})
        )
    _write_metar_csv(history / "2026-05-12.csv", rows)

    actual = MetarHistoryActualsProvider(
        history_root=history, min_samples=10
    ).collect(
        _make_target(station_id="RJAA"),
        StationMetadata(station_id="RJAA", longitude=0.0),
    )
    assert actual.status == "ok"
    assert actual.sample_count == 14


def test_metar_provider_emits_fahrenheit_resolution_value(tmp_path) -> None:
    history = tmp_path / "metar_history"
    _write_metar_csv(
        history / "2026-05-12.csv",
        _hourly_rows("KJFK", "2026-05-12", {hour: 25.0 for hour in range(24)}),
    )
    target = _make_target(
        slug="highest-temperature-in-new-york-on-may-12-2026",
        target_unit="fahrenheit",
        station_id="KJFK",
    )
    station = StationMetadata(station_id="KJFK", longitude=-73.78)

    actual = MetarHistoryActualsProvider(
        history_root=history, min_samples=10
    ).collect(target, station)

    assert actual.status == "ok"
    assert actual.observed_temp_c == 25.0
    assert actual.observed_temp_f == 77.0
    assert actual.observed_resolution_value == 77.0


def test_metar_default_min_samples_is_conservative() -> None:
    """The default lower bound on samples must keep us from declaring a
    daily max/min based on a sliver of the day. 12 ≈ half a day at the
    standard 60-minute METAR cadence."""
    assert METAR_MIN_SAMPLES_FOR_DAILY_EXTREME == 12


# ---- collect_actual_for_target fallback wiring -----------------------------


class _StubResponse:
    def __init__(self, status_code: int, body: dict | list | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.text = ""

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            from requests import HTTPError

            raise HTTPError(f"{self.status_code} Client Error")

    def json(self) -> dict | list:
        return self._body


def test_collect_actual_falls_back_to_metar_when_weather_com_403(
    monkeypatch, tmp_path
) -> None:
    """Reproduces the live failure mode: weather.com returns 403 for
    non-US ICAO codes, fallback aggregates the local METAR archive
    and returns an `ok` row whose notes carry the audit trail."""
    history = tmp_path / "metar_history"
    _write_metar_csv(
        history / "2026-05-12.csv",
        _hourly_rows("ZSPD", "2026-05-12", {hour: 26.0 + (hour % 5) for hour in range(24)}),
    )

    def boom(*args, **kwargs):
        return _StubResponse(403)

    monkeypatch.setattr("detect_temperature.sources.actuals.requests.get", boom)

    target = _make_target(
        slug="highest-temperature-in-shanghai-on-may-12-2026",
        target_date=date(2026, 5, 12),
        station_id="ZSPD",
        source_domain="wunderground.com",
    )
    station = StationMetadata(station_id="ZSPD", longitude=121.8, country="CN")

    actual = collect_actual_for_target(
        target,
        station,
        today=date(2026, 5, 14),
        finalization_lag_days=1,
        metar_history_root=history,
    )

    assert actual.status == "ok"
    assert actual.provider == "metar_history_archive"
    assert "fell back to metar_history_archive" in (actual.notes or "")
    assert "primary weather_com_historical failed" in (actual.notes or "")


def test_collect_actual_keeps_primary_error_when_metar_archive_empty(
    monkeypatch, tmp_path
) -> None:
    history = tmp_path / "metar_history"
    history.mkdir()  # empty: no per-day CSV present

    def boom(*args, **kwargs):
        return _StubResponse(403)

    monkeypatch.setattr("detect_temperature.sources.actuals.requests.get", boom)

    actual = collect_actual_for_target(
        _make_target(station_id="ZSPD", source_domain="wunderground.com"),
        StationMetadata(station_id="ZSPD", longitude=121.8, country="CN"),
        today=date(2026, 5, 14),
        finalization_lag_days=1,
        metar_history_root=history,
    )

    assert actual.status == "error"
    assert actual.provider == "weather_com_historical"
    assert "metar_fallback=pending" in (actual.notes or "")


def test_collect_actual_does_not_use_fallback_when_primary_ok(
    monkeypatch, tmp_path
) -> None:
    history = tmp_path / "metar_history"
    # If the fallback were called it would also return ok, so the test
    # would pass for the wrong reason. We make the fallback would-fail
    # by leaving history empty AND by also asserting provider name.
    history.mkdir()

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        return _StubResponse(
            200,
            {"observations": [{"temp": 12.5}, {"temp": 18.0}, {"temp": 16.0}]},
        )

    monkeypatch.setattr("detect_temperature.sources.actuals.requests.get", fake_get)

    target = _make_target(
        slug="highest-temperature-in-houston-on-may-12-2026",
        target_unit="celsius",
        station_id="KHOU",
        source_domain="wunderground.com",
    )
    station = StationMetadata(station_id="KHOU", longitude=-95.28, country="US")

    actual = collect_actual_for_target(
        target,
        station,
        today=date(2026, 5, 14),
        finalization_lag_days=1,
        metar_history_root=history,
    )

    assert actual.status == "ok"
    assert actual.provider == "weather_com_historical"
    assert actual.observed_temp_c == 18.0


def test_collect_actual_skips_fallback_for_pending_finalization_lag(tmp_path) -> None:
    """A `pending` returned for the finalization lag must NOT trigger
    the fallback — that day's data simply isn't ready yet."""
    history = tmp_path / "metar_history"
    history.mkdir()

    actual = collect_actual_for_target(
        _make_target(target_date=date(2026, 5, 14)),
        StationMetadata(station_id="RJAA", longitude=140.4),
        today=date(2026, 5, 14),  # same day, finalization lag = 1 -> pending
        finalization_lag_days=1,
        metar_history_root=history,
    )

    assert actual.status == "pending"
    assert "waiting for finalization lag" in (actual.notes or "")
    assert actual.provider == "none"


def test_collect_actual_respects_disable_via_none_history_root(monkeypatch) -> None:
    def boom(*args, **kwargs):
        return _StubResponse(403)

    monkeypatch.setattr("detect_temperature.sources.actuals.requests.get", boom)

    actual = collect_actual_for_target(
        _make_target(station_id="ZSPD", source_domain="wunderground.com"),
        StationMetadata(station_id="ZSPD", longitude=121.8, country="CN"),
        today=date(2026, 5, 14),
        finalization_lag_days=1,
        metar_history_root=None,
    )

    assert actual.status == "error"
    assert actual.provider == "weather_com_historical"
    assert "metar_fallback=" not in (actual.notes or "")
