"""Honest forward-style evaluation on Polymarket resolved events.

For each status=ok row in data/actuals.csv we fetch the Open-Meteo
*historical forecast* for that (station, date) — what the operational
NWP actually predicted — build the same feature row the live pipeline
would have built, run the production GBM + station bias correction,
and compare to observed.

This bypasses the issue that predictions_gbm.csv only covers today's
markets and that features.csv for past dates silently includes
look-ahead data.

Writes artifacts/forward_eval_real_events.csv + summary.json.
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
from typing import Any

import pandas as pd
import requests

from detect_temperature.features import stable_hash
from detect_temperature.models.baseline import ExtremeTemperatureBaseline
from detect_temperature.models.gbm import BiasCorrectedGBM
from detect_temperature.sources.aviation_weather import AviationWeatherStationCatalog
from detect_temperature.sources.base import CompositeStationCatalog
from detect_temperature.sources.manual import ManualStationCatalog


ROOT = Path(__file__).resolve().parents[1]

OPEN_METEO_HIST_FORECAST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
USER_AGENT = "detect-temperature/0.1 forward eval"


def fetch_forecast(lat: float, lon: float, day: date) -> dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "timezone": "UTC",
    }
    r = requests.get(OPEN_METEO_HIST_FORECAST, params=params,
                     headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    payload = r.json().get("daily") or {}
    def first(key: str) -> float | None:
        v = payload.get(key) or []
        return float(v[0]) if v else None
    return {
        "tmax": first("temperature_2m_max"),
        "tmin": first("temperature_2m_min"),
        "tmean": first("temperature_2m_mean"),
    }


def build_row(actual: dict[str, str], station: Any, forecast: dict[str, Any]) -> dict[str, Any] | None:
    if forecast.get("tmax") is None or forecast.get("tmin") is None:
        return None
    day = date.fromisoformat(actual["target_date"])
    doy = day.timetuple().tm_yday
    angle = 2 * math.pi * doy / 366.0
    is_max = 1 if actual["target_extreme"] == "max" else 0
    return {
        "slug": actual["slug"],
        "station_id": actual["station_id"],
        "target_date": actual["target_date"],
        "target_extreme": actual["target_extreme"],
        "station_latitude": station.latitude,
        "station_longitude": station.longitude,
        "station_elevation_m": station.elevation_m,
        "station_id_hash": stable_hash(actual["station_id"]),
        "source_domain_hash": stable_hash("wunderground.com"),
        "target_day_of_year_sin": math.sin(angle),
        "target_day_of_year_cos": math.cos(angle),
        "target_month": day.month,
        "target_is_weekend": int(day.weekday() >= 5),
        "target_is_max": is_max,
        "forecast_temp_max_c": forecast["tmax"],
        "forecast_temp_min_c": forecast["tmin"],
        "forecast_temp_mean_c": forecast["tmean"],
        "forecast_temp_spread_c": forecast["tmax"] - forecast["tmin"],
        "forecast_hourly_count": 24,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backtest_real_events")
    parser.add_argument("--actuals", default=str(ROOT / "data" / "actuals.csv"))
    parser.add_argument("--model", default=str(ROOT / "artifacts" / "models" / "gbm.joblib"))
    parser.add_argument("--calibration", default=str(ROOT / "data" / "station_calibration.csv"))
    parser.add_argument("--output-csv", default=str(ROOT / "artifacts" / "forward_eval_real_events.csv"))
    parser.add_argument("--output-json", default=str(ROOT / "artifacts" / "forward_eval_summary.json"))
    parser.add_argument("--sleep-s", type=float, default=0.8)
    args = parser.parse_args(argv)

    with open(args.actuals, newline="", encoding="utf-8") as fh:
        ok_rows = [r for r in csv.DictReader(fh) if r.get("status") == "ok"]
    if not ok_rows:
        print("no status=ok rows in actuals"); return 1
    print(f"resolved events: {len(ok_rows)}")

    catalog = CompositeStationCatalog([
        ManualStationCatalog(ROOT / "data" / "manual_stations.csv"),
        AviationWeatherStationCatalog(cache_path=ROOT / "data" / "stations.cache.json"),
    ])

    feature_rows: list[dict[str, Any]] = []
    for idx, actual in enumerate(ok_rows, 1):
        station = catalog.lookup(actual["station_id"])
        if station is None or station.latitude is None:
            print(f"[{idx}/{len(ok_rows)}] {actual['station_id']} SKIP: no station"); continue
        try:
            fcst = fetch_forecast(station.latitude, station.longitude,
                                  date.fromisoformat(actual["target_date"]))
        except Exception as exc:
            print(f"[{idx}/{len(ok_rows)}] {actual['station_id']} forecast FAIL: {exc}"); continue
        row = build_row(actual, station, fcst)
        if row is None:
            print(f"[{idx}/{len(ok_rows)}] {actual['station_id']} SKIP: empty forecast"); continue
        row["observed_temp_c"] = float(actual["observed_temp_c"])
        feature_rows.append(row)
        if args.sleep_s > 0:
            time.sleep(args.sleep_s)

    if not feature_rows:
        print("no usable rows"); return 1

    frame = pd.DataFrame(feature_rows)
    baseline = ExtremeTemperatureBaseline()
    frame["baseline_c"] = frame.apply(lambda r: baseline.predict_one(r.to_dict()), axis=1)
    model = BiasCorrectedGBM.load(args.model)
    frame["gbm_c"] = model.predict(frame)

    bias_map: dict[str, float] = {}
    cal_path = Path(args.calibration)
    if cal_path.exists():
        for r in csv.DictReader(cal_path.open()):
            try:
                bias_map[r["station_id"].strip().upper()] = float(r["rolling_bias_c"])
            except (KeyError, ValueError):
                continue

    frame["station_bias_c"] = frame["station_id"].map(bias_map).fillna(0.0)
    frame["corrected_c"] = frame["gbm_c"] - frame["station_bias_c"]

    for col, label in [("baseline_c", "baseline (raw Open-Meteo)"),
                       ("gbm_c", "GBM (no bias)"),
                       ("corrected_c", "GBM + station bias")]:
        err = frame[col] - frame["observed_temp_c"]
        abs_err = err.abs()
        n = len(err)
        print(f"{label:26s}  n={n}  MAE={abs_err.mean():.3f}  RMSE={(err**2).mean()**0.5:.3f}  "
              f"bias={err.mean():+.3f}  within-1C={(abs_err<=1).mean()*100:4.1f}%  "
              f"within-2C={(abs_err<=2).mean()*100:4.1f}%")

    Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_csv, index=False)

    summary = {
        "n_events": int(len(frame)),
        "model_path": args.model,
        "calibration_path": args.calibration,
        "metrics": {
            "baseline_mae_c": round(float((frame["baseline_c"] - frame["observed_temp_c"]).abs().mean()), 4),
            "gbm_mae_c":      round(float((frame["gbm_c"]      - frame["observed_temp_c"]).abs().mean()), 4),
            "corrected_mae_c":round(float((frame["corrected_c"]- frame["observed_temp_c"]).abs().mean()), 4),
        },
    }
    with open(args.output_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print(f"\nsaved {args.output_csv}  {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
