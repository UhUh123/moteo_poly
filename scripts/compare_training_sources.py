"""Compare synthetic-trained vs real-trained GBM on the same real holdout.

Runs two models on the tail of data/training_real.csv (last ~3 months):
  - synthetic_gbm: trained on data/training.csv (what we had before)
  - real_gbm:      trained on the earlier part of data/training_real.csv

Both are evaluated on the same holdout and the report lets us see whether
real-data training actually improves station-level MAE. Writes
artifacts/models/gbm_real.joblib on success.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from detect_temperature.evaluation import regression_metrics, time_ordered_split
from detect_temperature.models.baseline import ExtremeTemperatureBaseline
from detect_temperature.models.gbm import BiasCorrectedGBM, select_available_feature_columns


ROOT = Path(__file__).resolve().parents[1]


def _baseline_predictions(frame: pd.DataFrame) -> pd.Series:
    model = ExtremeTemperatureBaseline()
    return frame.apply(lambda row: model.predict_one(row.to_dict()), axis=1)


def _holdout_metrics(
    model: BiasCorrectedGBM | None,
    holdout: pd.DataFrame,
    label: str,
) -> list[dict]:
    h = holdout.copy()
    h["baseline_prediction_c"] = _baseline_predictions(h)
    if model is not None:
        h["corrected_prediction_c"] = model.predict(h)
    else:
        h["corrected_prediction_c"] = h["baseline_prediction_c"]
    rows = []
    for metric in regression_metrics(
        h,
        actual_column="observed_temp_c",
        prediction_column="baseline_prediction_c",
        model_name=f"{label}_baseline",
        split="holdout",
    ):
        rows.append(metric.to_record())
    for metric in regression_metrics(
        h,
        actual_column="observed_temp_c",
        prediction_column="corrected_prediction_c",
        model_name=f"{label}_corrected",
        split="holdout",
    ):
        rows.append(metric.to_record())
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compare_training_sources")
    parser.add_argument("--real", default=str(ROOT / "data" / "training_real.csv"))
    parser.add_argument("--synthetic", default=str(ROOT / "data" / "training.csv"))
    parser.add_argument("--output-json", default=str(ROOT / "artifacts" / "training_comparison.json"))
    parser.add_argument("--model-out", default=str(ROOT / "artifacts" / "models" / "gbm_real.joblib"))
    parser.add_argument("--test-fraction", type=float, default=0.15)
    args = parser.parse_args(argv)

    real = pd.read_csv(args.real).dropna(subset=["observed_temp_c"]).copy()
    synth = pd.read_csv(args.synthetic).dropna(subset=["observed_temp_c"]).copy()

    print(f"real rows={len(real)}  synthetic rows={len(synth)}")

    train_real, holdout_real, split = time_ordered_split(real, test_fraction=args.test_fraction)
    print(
        f"real split: train={len(train_real)} ({split['train_start']}..{split['train_end']})"
        f"  holdout={len(holdout_real)} ({split['test_start']}..{split['test_end']})"
    )

    feature_columns = select_available_feature_columns(train_real)
    print(f"feature columns ({len(feature_columns)}): {feature_columns}")

    # model A: synthetic-trained
    synth_train_features = select_available_feature_columns(synth)
    synth_model = BiasCorrectedGBM(feature_columns=synth_train_features)
    synth_model.fit(synth)

    # model B: real-trained on the earlier part
    real_model = BiasCorrectedGBM(feature_columns=feature_columns)
    real_model.fit(train_real)

    # Evaluate both on the real holdout (so metrics are comparable)
    metrics: list[dict] = []
    # baseline (Open-Meteo raw)
    metrics.extend(_holdout_metrics(None, holdout_real, "no_gbm"))
    # synthetic model needs its own columns; skip gracefully if mismatch
    try:
        metrics.extend(_holdout_metrics(synth_model, holdout_real, "synthetic_gbm"))
    except Exception as exc:
        print(f"synthetic model skipped: {exc}")
    metrics.extend(_holdout_metrics(real_model, holdout_real, "real_gbm"))

    combined = [m for m in metrics if m["group"] == "combined"]
    print("\nHoldout metrics (combined):")
    for m in combined:
        print(
            f"  {m['model']:35s} n={m['samples']:5d}  "
            f"MAE={m['mae_c']} RMSE={m['rmse_c']} bias={m['bias_c']}  "
            f"within1C={m['within_1c_pct']}% within2C={m['within_2c_pct']}%"
        )

    # Persist the real-trained model (refit on all real data for production)
    final = BiasCorrectedGBM(feature_columns=feature_columns)
    final.fit(real)
    Path(args.model_out).parent.mkdir(parents=True, exist_ok=True)
    final.save(args.model_out)
    print(f"\nsaved final real-data GBM -> {args.model_out}")

    payload = {
        "real_rows": int(len(real)),
        "synthetic_rows": int(len(synth)),
        "split": split,
        "feature_columns": feature_columns,
        "metrics": metrics,
    }
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"saved comparison report -> {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
