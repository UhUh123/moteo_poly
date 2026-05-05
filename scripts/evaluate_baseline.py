#!/usr/bin/env python3
"""Evaluate baseline forecast accuracy against historical observed data."""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from detect_temperature.sources.manual import ManualStationCatalog
from detect_temperature.sources.aviation_weather import AviationWeatherStationCatalog
from detect_temperature.sources.base import CompositeStationCatalog

FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "detect-temperature/0.1"


def fetch_forecast(lat: float, lon: float, start: date, end: date) -> dict:
    response = requests.get(
        FORECAST_ENDPOINT,
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "celsius",
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    hist_path = ROOT / "data" / "historical_observed.csv"
    if not hist_path.exists():
        print("historical_observed.csv not found")
        return 1

    # Read observed
    observed = {}
    with hist_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["station_id"], row["date"])
            observed[key] = {
                "max": float(row["observed_max_c"]),
                "min": float(row["observed_min_c"]),
            }

    # Load catalogs
    catalogs = []
    manual_path = ROOT / "data" / "manual_stations.csv"
    if manual_path.exists():
        catalogs.append(ManualStationCatalog(manual_path))
    cache_path = ROOT / "data" / "stations.cache.json"
    if cache_path.exists():
        catalogs.append(AviationWeatherStationCatalog(cache_path))
    catalog = CompositeStationCatalog(catalogs) if catalogs else None
    if not catalog:
        print("No station catalog")
        return 1

    # Group observed by station
    stations_dates = {}
    for sid, d in observed:
        stations_dates.setdefault(sid, set()).add(d)

    errors_max = []
    errors_min = []

    for sid in sorted(stations_dates):
        station = catalog.lookup(sid)
        if not station or station.latitude is None:
            print(f"  skip {sid}: no coordinates")
            continue

        dates = sorted(stations_dates[sid])
        start = date.fromisoformat(dates[0])
        end = date.fromisoformat(dates[-1])

        print(f"  fetching forecast for {sid} ({start} to {end}) ...")
        try:
            payload = fetch_forecast(station.latitude, station.longitude, start, end)
        except Exception as exc:
            print(f"    ERROR: {exc}")
            continue

        daily = payload.get("daily") or {}
        times = daily.get("time", [])
        max_vals = daily.get("temperature_2m_max", [])
        min_vals = daily.get("temperature_2m_min", [])

        for i, t in enumerate(times):
            key = (sid, t)
            if key not in observed:
                continue
            obs = observed[key]
            fmax = max_vals[i] if i < len(max_vals) else None
            fmin = min_vals[i] if i < len(min_vals) else None
            if fmax is not None:
                errors_max.append(fmax - obs["max"])
            if fmin is not None:
                errors_min.append(fmin - obs["min"])

    if not errors_max and not errors_min:
        print("No data to evaluate")
        return 1

    def _report(name: str, errors: list[float]) -> None:
        if not errors:
            return
        mae = sum(abs(e) for e in errors) / len(errors)
        rmse = (sum(e ** 2 for e in errors) / len(errors)) ** 0.5
        bias = sum(errors) / len(errors)
        print(f"\n{name}:")
        print(f"  samples : {len(errors)}")
        print(f"  MAE     : {mae:.2f} °C")
        print(f"  RMSE    : {rmse:.2f} °C")
        print(f"  bias    : {bias:+.2f} °C")

    print("\n" + "=" * 50)
    print("BASELINE ACCURACY (Open-Meteo forecast vs ERA5 observed)")
    print("=" * 50)
    _report("MAX temperature", errors_max)
    _report("MIN temperature", errors_min)

    # Combined
    all_errors = errors_max + errors_min
    _report("COMBINED", all_errors)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
