from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .base import StationMetadata

HISTORICAL_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = "detect-temperature/0.1"


class OpenMeteoHistoricalProvider:
    """Provides historical daily temperature extremes via Open-Meteo Archive API (ERA5)."""

    def __init__(
        self,
        endpoint: str = HISTORICAL_ENDPOINT,
        timeout_s: int = 30,
        timezone: str = "auto",
    ) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.timezone = timezone

    def daily_extremes(
        self,
        station: StationMetadata,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        if station.latitude is None or station.longitude is None:
            raise ValueError(f"Station {station.station_id} has no coordinates")

        response = requests.get(
            self.endpoint,
            params={
                "latitude": station.latitude,
                "longitude": station.longitude,
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "celsius",
                "timezone": self.timezone,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        daily = payload.get("daily") or {}
        times = daily.get("time", [])
        max_values = daily.get("temperature_2m_max", [])
        min_values = daily.get("temperature_2m_min", [])

        results = []
        for i, t in enumerate(times):
            results.append(
                {
                    "station_id": station.station_id,
                    "date": t,
                    "observed_max_c": _as_float(max_values[i]) if i < len(max_values) else None,
                    "observed_min_c": _as_float(min_values[i]) if i < len(min_values) else None,
                }
            )
        return results


def _as_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
