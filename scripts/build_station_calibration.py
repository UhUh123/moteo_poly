"""Compute per-station calibration from real historical training data.

For each station we compute, on a held-out period, the GBM-corrected
prediction error, then aggregate:

    rolling_mae_c   = mean(|predicted - observed|) per station
    rolling_bias_c  = mean(predicted - observed) per station

These two numbers replace the globally hard-coded sigma and drive
`signals.py` to use honest station-specific uncertainty.

Usage:
    PYTHONPATH=src python3 scripts/build_station_calibration.py \\
        --training data/training_real.csv \\
        --model artifacts/models/gbm.joblib \\
        --output data/station_calibration.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from detect_temperature.evaluation import time_ordered_split
from detect_temperature.models.baseline import ExtremeTemperatureBaseline
from detect_temperature.models.gbm import BiasCorrectedGBM


ROOT = Path(__file__).resolve().parents[1]


def _baseline_series(frame: pd.DataFrame) -> pd.Series:
    baseline = ExtremeTemperatureBaseline()
    return frame.apply(lambda row: baseline.predict_one(row.to_dict()), axis=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_station_calibration")
    parser.add_argument("--training", default=str(ROOT / "data" / "training_real.csv"))
    parser.add_argument("--model", default=str(ROOT / "artifacts" / "models" / "gbm.joblib"))
    parser.add_argument("--output", default=str(ROOT / "data" / "station_calibration.csv"))
    parser.add_argument("--test-fraction", type=float, default=0.15)
    parser.add_argument("--min-samples", type=int, default=30,
                        help="Drop stations with fewer than this many holdout samples.")
    args = parser.parse_args(argv)

    frame = pd.read_csv(args.training).dropna(subset=["observed_temp_c"]).copy()
    _, holdout, split = time_ordered_split(frame, test_fraction=args.test_fraction)
    print(f"holdout: {len(holdout)} rows, {split['test_start']} .. {split['test_end']}")

    model = BiasCorrectedGBM.load(args.model)
    holdout["baseline_prediction_c"] = _baseline_series(holdout)
    holdout["corrected_prediction_c"] = model.predict(holdout)
    holdout["error"] = holdout["corrected_prediction_c"] - holdout["observed_temp_c"]
    holdout["abs_error"] = holdout["error"].abs()

    rows: list[dict] = []
    for station_id, group in holdout.groupby("station_id"):
        if len(group) < args.min_samples:
            continue
        by_extreme = {}
        for extreme in ("max", "min"):
            sub = group[group["target_extreme"] == extreme]
            if len(sub) >= args.min_samples // 2:
                by_extreme[extreme] = {
                    "mae": round(float(sub["abs_error"].mean()), 4),
                    "bias": round(float(sub["error"].mean()), 4),
                    "n": int(len(sub)),
                }
        rows.append({
            "station_id": station_id,
            "rolling_mae_c": round(float(group["abs_error"].mean()), 4),
            "rolling_bias_c": round(float(group["error"].mean()), 4),
            "rolling_mae_max_c": by_extreme.get("max", {}).get("mae"),
            "rolling_bias_max_c": by_extreme.get("max", {}).get("bias"),
            "rolling_mae_min_c": by_extreme.get("min", {}).get("mae"),
            "rolling_bias_min_c": by_extreme.get("min", {}).get("bias"),
            "samples": int(len(group)),
            "holdout_start": split["test_start"],
            "holdout_end": split["test_end"],
        })

    if not rows:
        print("no stations met the sample threshold", flush=True)
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values("station_id").to_csv(out, index=False)

    maes = [r["rolling_mae_c"] for r in rows]
    biases = [r["rolling_bias_c"] for r in rows]
    print(f"wrote {len(rows)} stations -> {out}")
    print(f"MAE per station: min={min(maes):.2f} median={sorted(maes)[len(maes)//2]:.2f} max={max(maes):.2f}")
    print(f"bias range: {min(biases):+.2f} .. {max(biases):+.2f}")
    print("\nWorst 5 stations by MAE:")
    for r in sorted(rows, key=lambda x: -x["rolling_mae_c"])[:5]:
        print(f"  {r['station_id']}  MAE={r['rolling_mae_c']}  bias={r['rolling_bias_c']}  n={r['samples']}")
    print("\nBest 5 stations by MAE:")
    for r in sorted(rows, key=lambda x: x["rolling_mae_c"])[:5]:
        print(f"  {r['station_id']}  MAE={r['rolling_mae_c']}  bias={r['rolling_bias_c']}  n={r['samples']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
