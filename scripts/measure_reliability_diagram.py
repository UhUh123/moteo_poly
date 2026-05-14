"""Chapter 5 §6.3 + §7.2: reliability diagram + out-of-time holdout.

Read-only. Does not touch the production model on disk.

What it does
------------
1. Splits training_real.csv into:
   - in-time train  : everything up to (latest_date - holdout_days)
   - OOT holdout    : the last `holdout_days` strictly later

   The chapter's whole point in §7.2 is "the holdout must be later than
   the train". The current pipeline already does that via
   evaluation.time_ordered_split, but no one has ever measured how the
   honest out-of-time MAE compares to the in-sample one. Now we do.

2. Refits the same BiasCorrectedGBM on the in-time train.

3. On the OOT holdout:
   - reports MAE, RMSE, within-1C, within-2C
   - computes z = (observed - predicted) / sigma_station, where
     sigma_station comes from data/station_calibration.csv (or a
     supplied default). Under a perfectly calibrated Gaussian model
     z ~ N(0, 1).
   - prints a probability-integral-transform (PIT) histogram. If the
     model is calibrated, F(observed) = Phi(z) is uniform on [0, 1].
     Bumps near the edges = the model puts too little mass on the
     tails (fat-tail underestimation, exactly what chapter 2 found).
   - prints the empirical tail probabilities at |z| > 2, 3, 4 vs.
     what a Gaussian would predict.

Nothing is written to disk. Nothing is committed. The script only
prints to stdout. To save the metrics into artifacts/, pass
`--out artifacts/reliability_oot.json`.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from detect_temperature.models.baseline import ExtremeTemperatureBaseline  # noqa: E402
from detect_temperature.models.gbm import (  # noqa: E402
    BiasCorrectedGBM,
    select_available_feature_columns,
)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _two_tail(z: float) -> float:
    return 1.0 - math.erf(abs(z) / math.sqrt(2.0))


def _load_calibrations(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    out: dict[str, float] = {}
    for row in csv.DictReader(open(path, newline="", encoding="utf-8")):
        sid = (row.get("station_id") or "").strip().upper()
        try:
            mae = float(row.get("rolling_mae_c") or "")
        except ValueError:
            continue
        if sid:
            out[sid] = max(1.5, 1.5 * mae)  # same rule as signals.sigma_for_station
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--training", type=Path, default=ROOT / "data" / "training_real.csv")
    p.add_argument("--calibration", type=Path, default=ROOT / "data" / "station_calibration.csv")
    p.add_argument("--holdout-days", type=int, default=30)
    p.add_argument("--default-sigma", type=float, default=2.5)
    p.add_argument("--out", type=Path, default=None,
                   help="Optional JSON dump path for the metrics")
    args = p.parse_args()

    print(f"Loading {args.training}...")
    frame = pd.read_csv(args.training).dropna(subset=["observed_temp_c", "date"])
    frame["date"] = pd.to_datetime(frame["date"])

    last = frame["date"].max()
    cutoff = last - pd.Timedelta(days=args.holdout_days)
    train = frame[frame["date"] <= cutoff].copy()
    holdout = frame[frame["date"] > cutoff].copy()
    print(f"Train rows : {len(train):,}  ({train['date'].min().date()} .. {train['date'].max().date()})")
    print(f"OOT  rows  : {len(holdout):,}  ({holdout['date'].min().date()} .. {holdout['date'].max().date()})")
    if holdout.empty:
        print("OOT holdout is empty - increase --holdout-days or extend training_real.csv")
        return 1

    print("Refitting BiasCorrectedGBM on in-time train only...")
    available = select_available_feature_columns(train)
    print(f"  using {len(available)} feature columns")
    model = BiasCorrectedGBM(feature_columns=available)
    model.fit(train)

    holdout = holdout.copy()
    baseline = ExtremeTemperatureBaseline()
    holdout["baseline_c"] = holdout.apply(lambda r: baseline.predict_one(r.to_dict()), axis=1)
    holdout["pred_c"] = model.predict(holdout)
    holdout["err_c"] = holdout["pred_c"] - holdout["observed_temp_c"]

    mae = holdout["err_c"].abs().mean()
    rmse = (holdout["err_c"] ** 2).mean() ** 0.5
    bias = holdout["err_c"].mean()
    within_1 = (holdout["err_c"].abs() <= 1).mean() * 100
    within_2 = (holdout["err_c"].abs() <= 2).mean() * 100
    within_3 = (holdout["err_c"].abs() <= 3).mean() * 100

    print()
    print(f"=== Out-of-time holdout (the chapter 5 §7.2 honest test) ===")
    print(f"  n             {len(holdout):,}")
    print(f"  MAE           {mae:.3f} C")
    print(f"  RMSE          {rmse:.3f} C")
    print(f"  bias          {bias:+.3f} C")
    print(f"  within 1 C    {within_1:.1f}%")
    print(f"  within 2 C    {within_2:.1f}%")
    print(f"  within 3 C    {within_3:.1f}%")
    print()

    cals = _load_calibrations(args.calibration)
    print(f"Loaded {len(cals)} per-station sigmas (else default {args.default_sigma} C)")

    holdout["sigma_c"] = holdout["station_id"].map(
        lambda s: cals.get(str(s).upper(), args.default_sigma)
    )
    holdout["z"] = -(holdout["err_c"]) / holdout["sigma_c"]

    print()
    print("=== Reliability via PIT (chapter 5 §6.3) ===")
    print("If the model + sigma are honest, F(observed) = Phi(z) should be uniform on [0,1].")
    print("Bumps near 0/1 mean: actual outcomes land in the model's tails more often than it expects.")
    print()
    holdout["pit"] = holdout["z"].map(_phi)
    bins = 10
    hist = [0] * bins
    for v in holdout["pit"]:
        idx = min(int(v * bins), bins - 1)
        hist[idx] += 1
    n = len(holdout)
    expected = n / bins
    print(f"  bin     range          count  expected  bar")
    for i, c in enumerate(hist):
        lo, hi = i / bins, (i + 1) / bins
        bar = "#" * int(40 * c / max(hist))
        deviation_pct = 100 * (c - expected) / expected
        print(f"  {i:>3}   {lo:.1f}-{hi:.1f}    {c:>6}  {expected:>8.0f}  {bar} ({deviation_pct:+.0f}%)")
    print()

    print("=== Tail under-/over-coverage vs Gaussian ===")
    print(f"  |z|>k     observed_freq         gauss_freq           ratio")
    for k in (1, 2, 3, 4, 5):
        observed = (holdout["z"].abs() > k).mean()
        gauss = _two_tail(k)
        ratio = observed / gauss if gauss > 0 else float("inf")
        print(f"  >{k}        {observed * 100:>7.2f}%               {gauss * 100:>7.2f}%             {ratio:.1f}x")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "holdout_days": args.holdout_days,
            "n_train": int(len(train)),
            "n_holdout": int(len(holdout)),
            "mae_c": round(mae, 4),
            "rmse_c": round(rmse, 4),
            "bias_c": round(bias, 4),
            "within_1c_pct": round(within_1, 2),
            "within_2c_pct": round(within_2, 2),
            "within_3c_pct": round(within_3, 2),
            "pit_histogram": hist,
            "tail_coverage": {
                f"|z|>{k}": {
                    "observed": float((holdout["z"].abs() > k).mean()),
                    "gauss": _two_tail(k),
                } for k in (1, 2, 3, 4, 5)
            },
        }, indent=2))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
