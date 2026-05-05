from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

import requests

from .base import ForecastSnapshot, StationMetadata

FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "detect-temperature/0.1"


class OpenMeteoForecastProvider:
    def __init__(
        self,
        endpoint: str = FORECAST_ENDPOINT,
        timeout_s: int = 30,
        timezone: str = "auto",
    ) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.timezone = timezone

    def forecast_daily(self, station: StationMetadata, target_date: date) -> ForecastSnapshot:
        if station.latitude is None or station.longitude is None:
            raise ValueError(f"Station {station.station_id} has no coordinates")

        response = requests.get(
            self.endpoint,
            params={
                "latitude": station.latitude,
                "longitude": station.longitude,
                "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
                "hourly": "temperature_2m,dew_point_2m,relative_humidity_2m,pressure_msl,surface_pressure,wind_speed_10m",
                "temperature_unit": "celsius",
                "wind_speed_unit": "ms",
                "timezone": self.timezone,
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
            },
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        daily = payload.get("daily") or {}
        hourly = payload.get("hourly") or {}
        hourly_temps = tuple(
            value
            for value in (_as_float(item) for item in hourly.get("temperature_2m", []))
            if value is not None
        )

        return ForecastSnapshot(
            station_id=station.station_id,
            target_date=target_date,
            provider="open_meteo",
            temp_max_c=_first_float(daily, "temperature_2m_max"),
            temp_min_c=_first_float(daily, "temperature_2m_min"),
            temp_mean_c=_first_float(daily, "temperature_2m_mean") or (mean(hourly_temps) if hourly_temps else None),
            hourly_temperature_c=hourly_temps,
            raw=payload,
        )


def _first_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, list):
        value = value[0] if value else None
    return _as_float(value)


def _as_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
