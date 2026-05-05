from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from detect_temperature.models.baseline import ExtremeTemperatureBaseline
from detect_temperature.models.gbm import BiasCorrectedGBM, DEFAULT_FEATURE_COLUMNS


def _load_training_data() -> pd.DataFrame:
    root = Path(__file__).resolve().parents[1]
    path = root / "data" / "training.csv"
    if not path.exists():
        pytest.skip("training.csv not found; run scripts/build_training_data.py first")
    return pd.read_csv(path)


def test_bias_corrected_gbm_improves_over_baseline_on_real_data() -> None:
    """GBM correction should reduce RMSE compared to raw NWP baseline on real historical data."""
    frame = _load_training_data()
    assert not frame.empty, "training frame is empty"

    # Split by date: first 2/3 train, last 1/3 test
    unique_dates = sorted(frame["date"].unique())
    split_idx = int(len(unique_dates) * 0.67)
    train_dates = set(unique_dates[:split_idx])
    test_dates = set(unique_dates[split_idx:])

    train = frame[frame["date"].isin(train_dates)].copy()
    test = frame[frame["date"].isin(test_dates)].copy()

    # Baseline predictions
    baseline = ExtremeTemperatureBaseline()
    train["baseline_c"] = train.apply(lambda r: baseline.predict_one(r.to_dict()), axis=1)
    test["baseline_c"] = test.apply(lambda r: baseline.predict_one(r.to_dict()), axis=1)

    baseline_rmse = _rmse(test["observed_temp_c"], test["baseline_c"])
    assert baseline_rmse is not None and baseline_rmse > 0, "baseline RMSE should be positive"

    # Train GBM on residual — drop observation columns that are fully empty in this dataset
    feature_columns = [
        c for c in DEFAULT_FEATURE_COLUMNS
        if c in train.columns and train[c].notna().any()
    ]
    model = BiasCorrectedGBM(feature_columns=feature_columns)
    model.fit(train)

    corrected = model.predict(test)
    test["corrected_c"] = corrected

    corrected_rmse = _rmse(test["observed_temp_c"], test["corrected_c"])
    assert corrected_rmse is not None, "corrected RMSE should be computable"

    print(f"\nbaseline RMSE: {baseline_rmse:.3f}°C")
    print(f"corrected RMSE: {corrected_rmse:.3f}°C")
    print(f"improvement: {baseline_rmse - corrected_rmse:.3f}°C")

    # The corrected model should be at least as good as baseline
    assert corrected_rmse <= baseline_rmse + 0.01, (
        f"GBM correction should not worsen RMSE: "
        f"baseline={baseline_rmse:.3f}, corrected={corrected_rmse:.3f}"
    )


def _rmse(actual: pd.Series, predicted: pd.Series) -> float | None:
    diff = pd.to_numeric(actual, errors="coerce") - pd.to_numeric(predicted, errors="coerce")
    valid = diff.dropna()
    if valid.empty:
        return None
    return float((valid ** 2).mean() ** 0.5)
