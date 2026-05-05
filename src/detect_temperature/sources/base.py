from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class StationMetadata:
    station_id: str
    name: str = ""
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    country: str = ""
    source: str = ""


@dataclass(frozen=True)
class ForecastSnapshot:
    station_id: str
    target_date: date
    provider: str
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    temp_mean_c: float | None = None
    hourly_temperature_c: tuple[float, ...] = ()
    raw: dict | None = None

    @property
    def temp_spread_c(self) -> float | None:
        if self.temp_max_c is None or self.temp_min_c is None:
            return None
        return self.temp_max_c - self.temp_min_c


@dataclass(frozen=True)
class ObservationSnapshot:
    station_id: str
    provider: str
    observed_at: datetime | None = None
    temp_c: float | None = None
    dewpoint_c: float | None = None
    pressure_hpa: float | None = None
    wind_speed_mps: float | None = None
    raw: dict | None = None


class StationCatalog(Protocol):
    def lookup(self, station_id: str) -> StationMetadata | None:
        ...


class ForecastProvider(Protocol):
    def forecast_daily(self, station: StationMetadata, target_date: date) -> ForecastSnapshot:
        ...


class ObservationProvider(Protocol):
    def latest(self, station_id: str) -> ObservationSnapshot | None:
        ...


class CompositeStationCatalog:
    def __init__(self, catalogs: list[StationCatalog]) -> None:
        self.catalogs = catalogs

    def lookup(self, station_id: str) -> StationMetadata | None:
        for catalog in self.catalogs:
            station = catalog.lookup(station_id)
            if station is not None:
                return station
        return None
