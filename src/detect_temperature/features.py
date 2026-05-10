from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from .schema import MarketTarget
from .sources.base import ForecastSnapshot, ObservationSnapshot, StationMetadata
from .station_verifier import verify_target


def build_feature_row(
    target: MarketTarget,
    station: StationMetadata | None = None,
    forecast: ForecastSnapshot | None = None,
    observation: ObservationSnapshot | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    row = target.to_record()
    row.update(_target_features(target))
    row.update(_station_features(target, station))
    row.update(_forecast_features(forecast))
    row.update(_observation_features(observation, as_of=as_of))
    row.update(_verification_features(target, station))
    return row


def _target_features(target: MarketTarget) -> dict[str, Any]:
    if target.target_date is None:
        return {
            "target_day_of_year": None,
            "target_day_of_year_sin": None,
            "target_day_of_year_cos": None,
            "target_month": None,
            "target_is_weekend": None,
            "target_is_max": int(target.target_extreme == "max"),
        }

    day = target.target_date.timetuple().tm_yday
    angle = 2 * math.pi * day / 366.0
    return {
        "target_day_of_year": day,
        "target_day_of_year_sin": math.sin(angle),
        "target_day_of_year_cos": math.cos(angle),
        "target_month": target.target_date.month,
        "target_is_weekend": int(target.target_date.weekday() >= 5),
        "target_is_max": int(target.target_extreme == "max"),
    }


def _station_features(target: MarketTarget, station: StationMetadata | None) -> dict[str, Any]:
    station_id = station.station_id if station else target.station_id
    return {
        "station_latitude": station.latitude if station else None,
        "station_longitude": station.longitude if station else None,
        "station_elevation_m": station.elevation_m if station else None,
        "station_country": station.country if station else "",
        "station_id_hash": _stable_hash(station_id),
        "source_domain_hash": _stable_hash(target.source_domain),
        "has_station_coordinates": int(bool(station and station.latitude is not None and station.longitude is not None)),
    }


def _forecast_features(forecast: ForecastSnapshot | None) -> dict[str, Any]:
    if forecast is None:
        return {
            "forecast_provider": "",
            "forecast_temp_max_c": None,
            "forecast_temp_min_c": None,
            "forecast_temp_mean_c": None,
            "forecast_temp_spread_c": None,
            "forecast_hourly_count": 0,
        }
    return {
        "forecast_provider": forecast.provider,
        "forecast_temp_max_c": forecast.temp_max_c,
        "forecast_temp_min_c": forecast.temp_min_c,
        "forecast_temp_mean_c": forecast.temp_mean_c,
        "forecast_temp_spread_c": forecast.temp_spread_c,
        "forecast_hourly_count": len(forecast.hourly_temperature_c),
    }


def _observation_features(
    observation: ObservationSnapshot | None,
    as_of: datetime | None,
) -> dict[str, Any]:
    if observation is None:
        return {
            "observation_provider": "",
            "latest_observation_temp_c": None,
            "latest_observation_dewpoint_c": None,
            "latest_observation_pressure_hpa": None,
            "latest_observation_wind_speed_mps": None,
            "observation_age_hours": None,
        }

    age_hours = None
    if observation.observed_at is not None:
        reference = as_of or datetime.now(timezone.utc)
        observed_at = observation.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        age_hours = (reference - observed_at).total_seconds() / 3600.0

    return {
        "observation_provider": observation.provider,
        "latest_observation_temp_c": observation.temp_c,
        "latest_observation_dewpoint_c": observation.dewpoint_c,
        "latest_observation_pressure_hpa": observation.pressure_hpa,
        "latest_observation_wind_speed_mps": observation.wind_speed_mps,
        "observation_age_hours": age_hours,
    }


def stable_hash(value: str, modulo: int = 10_000) -> int:
    if not value:
        return 0
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def _verification_features(target: MarketTarget, station: StationMetadata | None) -> dict[str, Any]:
    verified, reason = verify_target(target, station)
    return {
        "station_verified": int(verified),
        "station_verification_reason": reason,
    }


def _stable_hash(value: str, modulo: int = 10_000) -> int:
    return stable_hash(value, modulo=modulo)
