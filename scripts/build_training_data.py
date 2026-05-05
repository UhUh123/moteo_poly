#!/usr/bin/env python3
"""Build real training data from historical observed + Open-Meteo forecast."""
from __future__ import annotations

import csv
import math
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from detect_temperature.sources.manual import ManualStationCatalog
from detect_temperature.sources.aviation_weather import AviationWeatherStationCatalog
from detect_temperature.sources.base import CompositeStationCatalog, StationMetadata
from detect_temperature.features import stable_hash

FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "detect-temperature/0.1"


def fetch_forecast_month(station: StationMetadata, start: date, end: date) -> list[dict]:
    response = requests.get(
        FORECAST_ENDPOINT,
        params={
            "latitude": station.latitude,
            "longitude": station.longitude,
            "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
            "temperature_unit": "celsius",
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily") or {}
    times = daily.get("time", [])
    max_vals = daily.get("temperature_2m_max", [])
    min_vals = daily.get("temperature_2m_min", [])
    mean_vals = daily.get("temperature_2m_mean", [])

    results = []
    for i, t in enumerate(times):
        results.append({
            "date": t,
            "forecast_temp_max_c": max_vals[i] if i < len(max_vals) else None,
            "forecast_temp_min_c": min_vals[i] if i < len(min_vals) else None,
            "forecast_temp_mean_c": mean_vals[i] if i < len(mean_vals) else None,
        })
    return results


def main() -> int:
    hist_path = ROOT / "data" / "historical_observed.csv"
    if not hist_path.exists():
        print("historical_observed.csv not found")
        return 1

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

    # Read observed and group by station
    observed_by_station: dict[str, dict[str, dict[str, float]]] = {}
    with hist_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sid = row["station_id"]
            d = row["date"]
            observed_by_station.setdefault(sid, {})[d] = {
                "max": float(row["observed_max_c"]),
                "min": float(row["observed_min_c"]),
            }

    output_path = ROOT / "data" / "training.csv"
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "station_id", "date", "target_extreme",
            "observed_temp_c",
            "station_latitude", "station_longitude", "station_elevation_m",
            "station_id_hash", "source_domain_hash",
            "target_day_of_year_sin", "target_day_of_year_cos",
            "target_month", "target_is_weekend", "target_is_max",
            "forecast_temp_max_c", "forecast_temp_min_c",
            "forecast_temp_mean_c", "forecast_temp_spread_c",
            "forecast_hourly_count",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        total_stations = len(observed_by_station)
        for idx, sid in enumerate(sorted(observed_by_station), 1):
            station = catalog.lookup(sid)
            if not station or station.latitude is None:
                print(f"[{idx}/{total_stations}] skip {sid}: no coordinates")
                continue

            dates = sorted(observed_by_station[sid])
            start = date.fromisoformat(dates[0])
            end = date.fromisoformat(dates[-1])

            print(f"[{idx}/{total_stations}] {sid} ({start} to {end}) ...")
            try:
                forecasts = fetch_forecast_month(station, start, end)
            except Exception as exc:
                print(f"    ERROR: {exc}")
                continue

            for f in forecasts:
                d = f["date"]
                if d not in observed_by_station[sid]:
                    continue
                obs = observed_by_station[sid][d]
                target_date = date.fromisoformat(d)
                day_of_year = target_date.timetuple().tm_yday
                angle = 2 * math.pi * day_of_year / 366.0
                fmax = f["forecast_temp_max_c"]
                fmin = f["forecast_temp_min_c"]
                fmean = f["forecast_temp_mean_c"]
                spread = (fmax - fmin) if fmax is not None and fmin is not None else None

                base = {
                    "station_id": sid,
                    "date": d,
                    "station_latitude": station.latitude,
                    "station_longitude": station.longitude,
                    "station_elevation_m": station.elevation_m,
                    "station_id_hash": stable_hash(sid),
                    "source_domain_hash": 0,
                    "target_day_of_year_sin": round(math.sin(angle), 6),
                    "target_day_of_year_cos": round(math.cos(angle), 6),
                    "target_month": target_date.month,
                    "target_is_weekend": int(target_date.weekday() >= 5),
                    "forecast_temp_max_c": fmax,
                    "forecast_temp_min_c": fmin,
                    "forecast_temp_mean_c": fmean,
                    "forecast_temp_spread_c": spread,
                    "forecast_hourly_count": 24,
                }

                # max row
                max_row = dict(base)
                max_row["target_extreme"] = "max"
                max_row["target_is_max"] = 1
                max_row["observed_temp_c"] = obs["max"]
                writer.writerow(max_row)

                # min row
                min_row = dict(base)
                min_row["target_extreme"] = "min"
                min_row["target_is_max"] = 0
                min_row["observed_temp_c"] = obs["min"]
                writer.writerow(min_row)

    print(f"\nDone -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
