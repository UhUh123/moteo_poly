from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class RegressionMetrics:
    split: str
    group: str
    model: str
    samples: int
    mae_c: float | None
    rmse_c: float | None
    bias_c: float | None
    within_1c_pct: float | None
    within_2c_pct: float | None
    within_3c_pct: float | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def time_ordered_split(
    frame: pd.DataFrame,
    date_column: str = "date",
    test_fraction: float = 0.33,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if date_column not in frame.columns:
        raise ValueError(f"Missing date column: {date_column}")
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between 0 and 1")

    dates = sorted(str(value) for value in frame[date_column].dropna().unique() if str(value))
    if len(dates) < 2:
        raise ValueError("Need at least two unique dates for a time-ordered split")

    split_idx = int(len(dates) * (1 - test_fraction))
    split_idx = max(1, min(split_idx, len(dates) - 1))
    train_dates = set(dates[:split_idx])
    test_dates = set(dates[split_idx:])

    train = frame[frame[date_column].astype(str).isin(train_dates)].copy()
    test = frame[frame[date_column].astype(str).isin(test_dates)].copy()
    metadata = {
        "date_column": date_column,
        "test_fraction": test_fraction,
        "train_dates": len(train_dates),
        "test_dates": len(test_dates),
        "train_start": dates[0],
        "train_end": dates[split_idx - 1],
        "test_start": dates[split_idx],
        "test_end": dates[-1],
    }
    return train, test, metadata


def regression_metrics(
    frame: pd.DataFrame,
    actual_column: str,
    prediction_column: str,
    model_name: str,
    split: str,
    group_column: str = "target_extreme",
) -> list[RegressionMetrics]:
    records = [_metric_for_group(frame, actual_column, prediction_column, model_name, split, "combined")]
    if group_column in frame.columns:
        for group in sorted(str(value) for value in frame[group_column].dropna().unique()):
            group_frame = frame[frame[group_column].astype(str) == group]
            records.append(_metric_for_group(group_frame, actual_column, prediction_column, model_name, split, group))
    return records


def _metric_for_group(
    frame: pd.DataFrame,
    actual_column: str,
    prediction_column: str,
    model_name: str,
    split: str,
    group: str,
) -> RegressionMetrics:
    actual = pd.to_numeric(frame.get(actual_column), errors="coerce")
    predicted = pd.to_numeric(frame.get(prediction_column), errors="coerce")
    errors = (predicted - actual).dropna()
    if errors.empty:
        return RegressionMetrics(
            split=split,
            group=group,
            model=model_name,
            samples=0,
            mae_c=None,
            rmse_c=None,
            bias_c=None,
            within_1c_pct=None,
            within_2c_pct=None,
            within_3c_pct=None,
        )

    abs_errors = errors.abs()
    return RegressionMetrics(
        split=split,
        group=group,
        model=model_name,
        samples=int(errors.shape[0]),
        mae_c=round(float(abs_errors.mean()), 4),
        rmse_c=round(float((errors.pow(2).mean()) ** 0.5), 4),
        bias_c=round(float(errors.mean()), 4),
        within_1c_pct=_within_pct(abs_errors, 1.0),
        within_2c_pct=_within_pct(abs_errors, 2.0),
        within_3c_pct=_within_pct(abs_errors, 3.0),
    )


def _within_pct(abs_errors: pd.Series, threshold: float) -> float:
    return round(float((abs_errors <= threshold).mean() * 100), 2)
