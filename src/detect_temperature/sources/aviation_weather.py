from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .base import ObservationSnapshot, StationMetadata

STATION_CACHE_URL = "https://aviationweather.gov/data/cache/stations.cache.json.gz"
METAR_ENDPOINT = "https://aviationweather.gov/api/data/metar"
USER_AGENT = "detect-temperature/0.1"


class AviationWeatherStationCatalog:
    def __init__(
        self,
        cache_path: str | Path = "data/stations.cache.json",
        cache_url: str = STATION_CACHE_URL,
        timeout_s: int = 60,
        verify_tls: bool = True,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.cache_url = cache_url
        self.timeout_s = timeout_s
        self.verify_tls = verify_tls
        self._stations: dict[str, StationMetadata] | None = None

    def refresh_cache(self) -> Path:
        response = requests.get(
            self.cache_url,
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_s,
            verify=self.verify_tls,
        )
        response.raise_for_status()
        content = response.content
        try:
            decoded = gzip.decompress(content)
        except OSError:
            decoded = content

        payload = json.loads(decoded.decode("utf-8"))
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._stations = None
        return self.cache_path

    def lookup(self, station_id: str) -> StationMetadata | None:
        if not station_id:
            return None
        stations = self._load()
        return stations.get(station_id.upper())

    def _load(self) -> dict[str, StationMetadata]:
        if self._stations is not None:
            return self._stations
        if not self.cache_path.exists():
            self._stations = {}
            return self._stations

        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        stations: dict[str, StationMetadata] = {}
        for item in _iter_station_items(payload):
            station = _station_from_item(item)
            if station is None:
                continue
            stations[station.station_id.upper()] = station
        self._stations = stations
        return stations


class AviationWeatherMetarProvider:
    def __init__(
        self,
        endpoint: str = METAR_ENDPOINT,
        timeout_s: int = 30,
        verify_tls: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.verify_tls = verify_tls

    def latest(self, station_id: str) -> ObservationSnapshot | None:
        if not station_id:
            return None
        response = requests.get(
            self.endpoint,
            params={"ids": station_id.upper(), "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_s,
            verify=self.verify_tls,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        payload = response.json()
        if not payload:
            return None
        item = payload[0] if isinstance(payload, list) else payload
        return ObservationSnapshot(
            station_id=station_id.upper(),
            provider="aviationweather_metar",
            observed_at=_parse_datetime(_first(item, "obsTime", "reportTime", "receiptTime")),
            temp_c=_as_float(_first(item, "temp", "temp_c")),
            dewpoint_c=_as_float(_first(item, "dewp", "dewpoint", "dewpoint_c")),
            pressure_hpa=_pressure_hpa(_first(item, "slp", "altim", "pressure")),
            wind_speed_mps=_knots_to_mps(_first(item, "wspd", "windSpeed")),
            raw=item,
        )


def _iter_station_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("stations", "data", "items"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("features"), list):
        items = []
        for feature in payload["features"]:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            merged = dict(props)
            if isinstance(geometry, dict) and isinstance(geometry.get("coordinates"), list):
                coords = geometry["coordinates"]
                if len(coords) >= 2:
                    merged.setdefault("lon", coords[0])
                    merged.setdefault("lat", coords[1])
            items.append(merged)
        return items
    return []


def _station_from_item(item: dict) -> StationMetadata | None:
    station_id = _first(item, "icaoId", "icao", "id", "station_id", "stationId")
    if not station_id:
        return None
    return StationMetadata(
        station_id=str(station_id).upper(),
        name=str(_first(item, "site", "name", "stationName") or ""),
        latitude=_as_float(_first(item, "lat", "latitude")),
        longitude=_as_float(_first(item, "lon", "longitude")),
        elevation_m=_as_float(_first(item, "elev", "elevation", "elevation_m")),
        country=str(_first(item, "country", "countryCode") or ""),
        source="aviationweather_station_cache",
    )


def _first(item: dict, *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in {"", None}:
            return item[key]
    return None


def _as_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pressure_hpa(value: Any) -> float | None:
    pressure = _as_float(value)
    if pressure is None:
        return None
    if pressure < 100:
        return pressure * 33.8638866667
    return pressure


def _knots_to_mps(value: Any) -> float | None:
    wind = _as_float(value)
    if wind is None:
        return None
    return wind * 0.514444


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
