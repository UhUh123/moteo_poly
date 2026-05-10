"""Weekly refresh of the temperature pipeline calibration.

What this does, end-to-end, in one command:
  1. For every station in data/training_stations.json, pull the most
     recent `--window-days` of Open-Meteo forecast + ERA5 observed
     pairs (by default, the last 180 days). This captures the latest
     station behaviour — most useful when a model frozen 3-6 months
     ago starts drifting as the season changes.
  2. Merge those fresh rows into data/training_real.csv, deduplicating
     on (station_id, date, target_extreme). Older rows beyond the
     merge window are kept.
  3. Re-train the production GBM and overwrite artifacts/models/gbm.joblib.
  4. Re-build data/station_calibration.csv using the newest holdout.
  5. Refresh predictions and market signals so the next run of the
     paper dashboard picks up the new model.

Usage:
  PYTHONPATH=src python3 scripts/refresh_calibration.py
  PYTHONPATH=src python3 scripts/refresh_calibration.py --window-days 90
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, cwd=ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="refresh_calibration")
    parser.add_argument("--window-days", type=int, default=180,
                        help="Pull the last N days of station data. 180 = one full season cycle.")
    parser.add_argument("--stations", default=str(ROOT / "data" / "training_stations.json"))
    parser.add_argument("--training", default=str(ROOT / "data" / "training_real.csv"))
    parser.add_argument("--model", default=str(ROOT / "artifacts" / "models" / "gbm.joblib"))
    parser.add_argument("--calibration", default=str(ROOT / "data" / "station_calibration.csv"))
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Reuse existing training CSV without hitting Open-Meteo.")
    parser.add_argument("--skip-signals", action="store_true",
                        help="Skip the regenerate-signals step at the end.")
    args = parser.parse_args(argv)

    # ERA5 archive has a ~5 day lag — trim the window so we don't 400.
    ARCHIVE_LAG_DAYS = 7
    end = date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
    start = end - timedelta(days=args.window_days)
    print(f"Refresh window: {start} -> {end} ({args.window_days} days; "
          f"end trimmed {ARCHIVE_LAG_DAYS}d back for ERA5 archive lag)")

    staging = ROOT / "data" / "training_refresh_window.csv"
    if not args.skip_fetch:
        _run([
            sys.executable, "scripts/build_historical_training.py",
            "--stations", args.stations,
            "--output", str(staging),
            "--start", start.isoformat(),
            "--end", end.isoformat(),
            "--sleep-s", "0.6",
        ])
    else:
        print(f"--skip-fetch set, reusing existing {staging}")
        if not staging.exists():
            print(f"ERROR: {staging} does not exist"); return 1

    # Merge fresh rows into the canonical training set
    merge_into(Path(args.training), staging)

    # Retrain the production model
    _run([
        sys.executable, "-m", "detect_temperature.cli", "train-gbm",
        "--training", args.training,
        "--model", args.model,
        "--metrics", str(ROOT / "artifacts" / "model_metrics.json"),
        "--holdout-predictions", str(ROOT / "artifacts" / "holdout_predictions.csv"),
        "--report", str(ROOT / "artifacts" / "model_report.md"),
    ])

    # Recompute per-station calibration on the fresh model
    _run([
        sys.executable, "scripts/build_station_calibration.py",
        "--training", args.training,
        "--model", args.model,
        "--output", args.calibration,
    ])

    if not args.skip_signals:
        # Rebuild predictions and signals so the dashboard is immediately fresh
        _run([
            sys.executable, "-m", "detect_temperature.cli", "predict-gbm",
            "--features", str(ROOT / "data" / "features.csv"),
            "--model", args.model,
            "--output", str(ROOT / "artifacts" / "predictions_gbm.csv"),
            "--station-calibration", args.calibration,
        ])
        _run([
            sys.executable, "-m", "detect_temperature.cli", "build-market-signals",
            "--risk-profile", "bankroll_100",
            "--markets", str(ROOT / "data" / "polymarket_weather_markets.csv"),
            "--predictions", str(ROOT / "artifacts" / "predictions_gbm.csv"),
            "--output", str(ROOT / "artifacts" / "market_signals.csv"),
        ])

    print("\nrefresh complete. Model, calibration, predictions and signals are up to date.")
    return 0


def merge_into(canonical: Path, fresh: Path) -> None:
    """Union (station_id, date, target_extreme) rows, preferring fresh ones."""
    if not fresh.exists():
        print(f"WARN: {fresh} missing — nothing to merge")
        return
    fresh_df = pd.read_csv(fresh)
    if fresh_df.empty:
        print("WARN: fresh CSV empty"); return
    if canonical.exists():
        old_df = pd.read_csv(canonical)
        key = ["station_id", "date", "target_extreme"]
        fresh_keys = fresh_df[key].apply(tuple, axis=1)
        old_keys = old_df[key].apply(tuple, axis=1)
        surviving_old = old_df[~old_keys.isin(fresh_keys)]
        merged = pd.concat([surviving_old, fresh_df], ignore_index=True)
        merged = merged.sort_values(["station_id", "date", "target_extreme"])
        merged.to_csv(canonical, index=False)
        print(f"merged {len(fresh_df)} fresh rows into {canonical}: "
              f"kept {len(surviving_old)} old + added {len(fresh_df)} = {len(merged)} total")
    else:
        fresh_df.to_csv(canonical, index=False)
        print(f"no existing canonical file; wrote {len(fresh_df)} rows -> {canonical}")


if __name__ == "__main__":
    sys.exit(main())
