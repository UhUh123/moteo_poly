from __future__ import annotations

import math
from datetime import date

from detect_temperature.features import build_feature_row
from detect_temperature.schema import MarketTarget
from detect_temperature.sources.base import StationMetadata, ForecastSnapshot, ObservationSnapshot


def test_feature_row_without_any_extra_data() -> None:
    """build_feature_row should work with only MarketTarget and fill None/defaults."""
    target = MarketTarget(
        title="Test",
        slug="test",
        city="Test City",
        location_name="",
        target_date=date(2025, 6, 15),
        target_extreme="max",
        target_unit="celsius",
        station_id="TEST",
        resolution_source_url="",
        source_domain="",
    )
    row = build_feature_row(target)

    assert row["slug"] == "test"
    assert row["target_is_max"] == 1
    assert row["target_month"] == 6
    assert row["station_latitude"] is None
    assert row["forecast_temp_max_c"] is None
    assert row["latest_observation_temp_c"] is None


def test_feature_row_with_station_and_forecast() -> None:
    """build_feature_row should include station metadata and forecast values."""
    target = MarketTarget(
        title="Test",
        slug="test",
        city="Test City",
        location_name="",
        target_date=date(2025, 1, 1),
        target_extreme="min",
        target_unit="celsius",
        station_id="TEST",
        resolution_source_url="https://example.com/data",
        source_domain="example.com",
    )
    station = StationMetadata(
        station_id="TEST",
        name="Test Station",
        latitude=40.0,
        longitude=-74.0,
        elevation_m=10.0,
        country="US",
    )
    forecast = ForecastSnapshot(
        station_id="TEST",
        target_date=date(2025, 1, 1),
        provider="open_meteo",
        temp_max_c=20.0,
        temp_min_c=5.0,
        temp_mean_c=12.5,
        hourly_temperature_c=(10.0, 12.0, 14.0),
    )

    row = build_feature_row(target, station, forecast)

    assert row["station_latitude"] == 40.0
    assert row["station_longitude"] == -74.0
    assert row["station_elevation_m"] == 10.0
    assert row["has_station_coordinates"] == 1
    assert row["forecast_temp_max_c"] == 20.0
    assert row["forecast_temp_min_c"] == 5.0
    assert row["forecast_temp_spread_c"] == 15.0
    assert row["forecast_hourly_count"] == 3
    assert row["target_is_max"] == 0

    # Calendar features for Jan 1
    day = 1
    angle = 2 * math.pi * day / 366.0
    assert row["target_day_of_year"] == day
    assert row["target_day_of_year_sin"] == pytest.approx(math.sin(angle))
    assert row["target_is_weekend"] == 0  # 2025-01-01 is Wednesday


def test_feature_row_hashes_are_stable() -> None:
    """Station and domain hashes should be deterministic."""
    target = MarketTarget(
        title="Test",
        slug="test",
        city="City",
        location_name="",
        target_date=None,
        target_extreme="unknown",
        target_unit="unknown",
        station_id="ST123",
        resolution_source_url="",
        source_domain="wunderground.com",
    )
    row1 = build_feature_row(target)
    row2 = build_feature_row(target)
    assert row1["station_id_hash"] == row2["station_id_hash"]
    assert row1["source_domain_hash"] == row2["source_domain_hash"]
    assert row1["station_id_hash"] != 0
    assert row1["source_domain_hash"] != 0


import pytest  # noqa: E402
