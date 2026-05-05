from __future__ import annotations

import csv
from pathlib import Path

from .base import StationMetadata


class ManualStationCatalog:
    def __init__(self, path: str | Path = "data/manual_stations.csv") -> None:
        self.path = Path(path)
        self._stations: dict[str, StationMetadata] | None = None

    def lookup(self, station_id: str) -> StationMetadata | None:
        if not station_id:
            return None
        return self._load().get(station_id.upper())

    def _load(self) -> dict[str, StationMetadata]:
        if self._stations is not None:
            return self._stations
        if not self.path.exists():
            self._stations = {}
            return self._stations

        stations: dict[str, StationMetadata] = {}
        with self.path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                station_id = (row.get("station_id") or "").upper()
                if not station_id:
                    continue
                stations[station_id] = StationMetadata(
                    station_id=station_id,
                    name=row.get("name", ""),
                    latitude=_as_float(row.get("latitude")),
                    longitude=_as_float(row.get("longitude")),
                    elevation_m=_as_float(row.get("elevation_m")),
                    country=row.get("country", ""),
                    source=row.get("source", "manual_station_catalog"),
                )
        self._stations = stations
        return stations


def _as_float(value: str | None) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except ValueError:
        return None

