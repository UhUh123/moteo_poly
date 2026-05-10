"""Build a real historical training set for the temperature GBM.

For each verified ICAO station in data/training_stations.json we collect
  - historical observed daily max / min from Open-Meteo ERA5 archive
    (ERA5 is a ~0.25° reanalysis; station agreement is ~0.3–0.5 °C MAE,
    which is good enough for training a bias-correction GBM.)
  - day-ahead forecast max / min from Open-Meteo Historical Forecast API
    (this is what the operational model actually predicted)

and emit rows compatible with data/training.csv schema, so that
`detect-temperature train-gbm` works without any further code changes.

Default window: 2023-01-01 .. 2026-04-30 (avoids overlap with our current
resolved Polymarket actuals around 2026-05). That is ~3.3 years of data.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from detect_temperature.features import stable_hash

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATIONS = ROOT / "data" / "training_stations.json"
DEFAULT_OUTPUT = ROOT / "data" / "training_real.csv"

OPEN_METEO_HIST_FORECAST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = "detect-temperature/0.1 training builder"

TRAINING_COLUMNS = [
    "station_id",
    "date",
    "target_extreme",
    "observed_temp_c",
    "station_latitude",
    "station_longitude",
    "station_elevation_m",
    "station_id_hash",
    "source_domain_hash",
    "target_day_of_year_sin",
    "target_day_of_year_cos",
    "target_month",
    "target_is_weekend",
    "target_is_max",
    "forecast_temp_max_c",
    "forecast_temp_min_c",
    "forecast_temp_mean_c",
    "forecast_temp_spread_c",
    "forecast_hourly_count",
]


def load_stations(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def pull_observed(station: dict, start: date, end: date) -> pd.DataFrame:
    """Observed daily max/min from Open-Meteo ERA5 archive."""
    params = {
        "latitude": station["lat"],
        "longitude": station["lon"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "UTC",
    }
    response = requests.get(
        OPEN_METEO_ARCHIVE, params=params, headers={"User-Agent": USER_AGENT}, timeout=60
    )
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "date": pd.to_datetime(times),
            "observed_max_c": daily.get("temperature_2m_max") or [None] * len(times),
            "observed_min_c": daily.get("temperature_2m_min") or [None] * len(times),
        }
    ).set_index("date")


def pull_forecast(station: dict, start: date, end: date) -> pd.DataFrame:
    """Historical operational-model forecast (what the NWP predicted at the time)."""
    params = {
        "latitude": station["lat"],
        "longitude": station["lon"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "timezone": "UTC",
    }
    response = requests.get(
        OPEN_METEO_HIST_FORECAST, params=params, headers={"User-Agent": USER_AGENT}, timeout=60
    )
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "date": pd.to_datetime(times),
            "forecast_temp_max_c": daily.get("temperature_2m_max") or [None] * len(times),
            "forecast_temp_min_c": daily.get("temperature_2m_min") or [None] * len(times),
            "forecast_temp_mean_c": daily.get("temperature_2m_mean") or [None] * len(times),
        }
    ).set_index("date")


def merge_rows(station: dict, obs: pd.DataFrame, forecast: pd.DataFrame) -> list[dict]:
    if obs.empty or forecast.empty:
        return []
    joined = obs.join(forecast, how="inner")
    rows: list[dict] = []
    station_hash = stable_hash(station["id"])
    source_hash = stable_hash("wunderground.com")  # placeholder: training has no resolution source
    for ts, row in joined.iterrows():
        observed_max = row.get("observed_max_c")
        observed_min = row.get("observed_min_c")
        f_max = row.get("forecast_temp_max_c")
        f_min = row.get("forecast_temp_min_c")
        f_mean = row.get("forecast_temp_mean_c")
        if pd.isna(f_max) or pd.isna(f_min):
            continue
        spread = float(f_max) - float(f_min)

        day = ts.date() if hasattr(ts, "date") else ts
        day_of_year = day.timetuple().tm_yday
        angle = 2.0 * math.pi * day_of_year / 366.0
        base = {
            "station_id": station["id"],
            "date": day.isoformat(),
            "station_latitude": station["lat"],
            "station_longitude": station["lon"],
            "station_elevation_m": station.get("elev"),
            "station_id_hash": station_hash,
            "source_domain_hash": source_hash,
            "target_day_of_year_sin": math.sin(angle),
            "target_day_of_year_cos": math.cos(angle),
            "target_month": day.month,
            "target_is_weekend": int(day.weekday() >= 5),
            "forecast_temp_max_c": float(f_max),
            "forecast_temp_min_c": float(f_min),
            "forecast_temp_mean_c": None if pd.isna(f_mean) else float(f_mean),
            "forecast_temp_spread_c": spread,
            "forecast_hourly_count": 24,
        }
        if not pd.isna(observed_max):
            rows.append({
                **base,
                "target_extreme": "max",
                "observed_temp_c": float(observed_max),
                "target_is_max": 1,
            })
        if not pd.isna(observed_min):
            rows.append({
                **base,
                "target_extreme": "min",
                "observed_temp_c": float(observed_min),
                "target_is_max": 0,
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_historical_training")
    parser.add_argument("--stations", default=str(DEFAULT_STATIONS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2026-04-30")
    parser.add_argument("--limit-stations", type=int, default=None,
                        help="Process only first N stations (smoke tests).")
    parser.add_argument("--sleep-s", type=float, default=1.0,
                        help="Sleep between stations to be polite to APIs.")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    stations = load_stations(Path(args.stations))
    if args.limit_stations:
        stations = stations[: args.limit_stations]

    print(f"stations: {len(stations)}, range: {start} .. {end}")

    all_rows: list[dict] = []
    for idx, station in enumerate(stations, 1):
        print(f"[{idx}/{len(stations)}] {station['id']} ...", end=" ", flush=True)
        try:
            obs = pull_observed(station, start, end)
            forecast = pull_forecast(station, start, end)
            rows = merge_rows(station, obs, forecast)
            all_rows.extend(rows)
            print(f"obs={len(obs)} fc={len(forecast)} rows={len(rows)}")
        except Exception as exc:
            print(f"FAILED: {exc}")
        if args.sleep_s > 0:
            time.sleep(args.sleep_s)

    if not all_rows:
        print("no rows produced; nothing to write", file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TRAINING_COLUMNS)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({col: row.get(col) for col in TRAINING_COLUMNS})
    print(f"\nwrote {len(all_rows)} rows -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
