"""Chapter 6 §10.2 + chapter 5 §9.5: climatology as honest baseline.

The chapter is blunt: if our GBM does not beat the trivial baseline of
"average over the last N years for this same calendar day at this
station", then GBM is doing nothing useful.

This is read-only. It loads training_real.csv and computes:

  For each (station, target_date) in the last `holdout_days` days:
    1. Climatology pred = mean of observed_temp_c over the SAME
       calendar day (+/- 7 days) in earlier years.
    2. Open-Meteo raw pred = forecast_temp_max_c (or _min_c).
    3. GBM pred = corrected via the production model.

  Then compare MAE for each across the holdout.

If climatology MAE is comparable to GBM MAE the project is doing
nothing on top of "look at the historical average". That would
be a brutal but honest conclusion.

No production code is touched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from detect_temperature.models.baseline import ExtremeTemperatureBaseline  # noqa: E402
from detect_temperature.models.gbm import (  # noqa: E402
    BiasCorrectedGBM,
    select_available_feature_columns,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--training", type=Path, default=ROOT / "data" / "training_real.csv")
    p.add_argument("--holdout-days", type=int, default=30)
    p.add_argument("--clim-window-days", type=int, default=7,
                   help="±days around the same calendar date to average over earlier years")
    args = p.parse_args()

    print(f"Loading {args.training}...")
    frame = pd.read_csv(args.training).dropna(subset=["observed_temp_c", "date"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["doy"] = frame["date"].dt.dayofyear

    last = frame["date"].max()
    cutoff = last - pd.Timedelta(days=args.holdout_days)
    train = frame[frame["date"] <= cutoff].copy()
    holdout = frame[frame["date"] > cutoff].copy()
    print(f"Train rows : {len(train):,}  ({train['date'].min().date()} .. {train['date'].max().date()})")
    print(f"OOT  rows  : {len(holdout):,}  ({holdout['date'].min().date()} .. {holdout['date'].max().date()})")

    # GBM trained only on the in-time train
    available = select_available_feature_columns(train)
    model = BiasCorrectedGBM(feature_columns=available)
    model.fit(train)
    holdout = holdout.copy()
    holdout["gbm_c"] = model.predict(holdout)

    # Open-Meteo raw baseline
    baseline = ExtremeTemperatureBaseline()
    holdout["raw_c"] = holdout.apply(lambda r: baseline.predict_one(r.to_dict()), axis=1)

    # Climatology: same station, same target_extreme, same +/- doy window, only earlier years
    print("Computing climatology (this is O(holdout x clim_window x train)) ...")
    train_indexed = train[["station_id", "target_extreme", "doy", "observed_temp_c"]].copy()
    win = args.clim_window_days
    clim_predictions = []
    for _, row in holdout.iterrows():
        sid = row["station_id"]
        ext = row["target_extreme"]
        doy = int(row["doy"])
        # circular window over day-of-year
        window = set(((doy - 1 + d) % 365) + 1 for d in range(-win, win + 1))
        sub = train_indexed[
            (train_indexed["station_id"] == sid)
            & (train_indexed["target_extreme"] == ext)
            & (train_indexed["doy"].isin(window))
        ]
        clim_predictions.append(sub["observed_temp_c"].mean() if len(sub) else float("nan"))
    holdout["clim_c"] = clim_predictions
    holdout = holdout.dropna(subset=["clim_c"])

    print()
    print(f"=== OOT comparison on {len(holdout):,} rows ({args.holdout_days}-day holdout) ===")
    for label, col in [
        ("Climatology (no model)        ", "clim_c"),
        ("Open-Meteo raw                ", "raw_c"),
        ("GBM (production-equivalent)   ", "gbm_c"),
    ]:
        err = (holdout[col] - holdout["observed_temp_c"]).dropna()
        mae = err.abs().mean()
        rmse = (err ** 2).mean() ** 0.5
        bias = err.mean()
        within_1 = (err.abs() <= 1).mean() * 100
        within_2 = (err.abs() <= 2).mean() * 100
        print(f"  {label}  n={len(err):>5}  MAE={mae:.3f} C  RMSE={rmse:.3f}  bias={bias:+.3f}  "
              f"within-1C={within_1:.1f}%  within-2C={within_2:.1f}%")

    print()
    raw_mae = (holdout["raw_c"] - holdout["observed_temp_c"]).abs().mean()
    gbm_mae = (holdout["gbm_c"] - holdout["observed_temp_c"]).abs().mean()
    clim_mae = (holdout["clim_c"] - holdout["observed_temp_c"]).abs().mean()
    print("Reading guide:")
    print(f"  Climatology MAE = {clim_mae:.2f} C - the floor any honest model must beat.")
    print(f"  Open-Meteo MAE  = {raw_mae:.2f} C - what GBM has to improve on.")
    print(f"  GBM MAE         = {gbm_mae:.2f} C")
    print()
    print(f"  Open-Meteo over climatology: {(clim_mae - raw_mae):+.3f} C  "
          f"({(clim_mae - raw_mae) / clim_mae * 100:+.1f}%)")
    print(f"  GBM over climatology:        {(clim_mae - gbm_mae):+.3f} C  "
          f"({(clim_mae - gbm_mae) / clim_mae * 100:+.1f}%)")
    print(f"  GBM over Open-Meteo:         {(raw_mae - gbm_mae):+.3f} C  "
          f"({(raw_mae - gbm_mae) / raw_mae * 100:+.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
