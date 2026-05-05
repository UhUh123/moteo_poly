from __future__ import annotations

import csv
import json
from datetime import date, timedelta

import pandas as pd

from detect_temperature.pipeline import predict_gbm, train_gbm_model


def test_train_gbm_model_writes_artifacts(tmp_path) -> None:
    training_path = tmp_path / "training.csv"
    _write_training_fixture(training_path)

    model_path = tmp_path / "models" / "gbm.joblib"
    metrics_path = tmp_path / "metrics.json"
    holdout_path = tmp_path / "holdout.csv"
    report_path = tmp_path / "report.md"

    summary = train_gbm_model(
        training_path=training_path,
        model_path=model_path,
        metrics_path=metrics_path,
        holdout_predictions_path=holdout_path,
        report_path=report_path,
        test_fraction=0.25,
    )

    assert model_path.exists()
    assert metrics_path.exists()
    assert holdout_path.exists()
    assert report_path.exists()
    assert summary["rows"] == 24
    assert summary["holdout_rows"] == 6
    assert "forecast_temp_max_c" in summary["feature_columns"]

    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["metrics"]
    assert any(metric["group"] == "combined" for metric in payload["metrics"])


def test_predict_gbm_writes_corrected_predictions(tmp_path) -> None:
    training_path = tmp_path / "training.csv"
    _write_training_fixture(training_path)
    model_path = tmp_path / "gbm.joblib"
    train_gbm_model(training_path=training_path, model_path=model_path, test_fraction=0.25)

    features_path = tmp_path / "features.csv"
    frame = pd.read_csv(training_path).drop(columns=["observed_temp_c"])
    frame["slug"] = [f"slug-{idx}" for idx in range(len(frame))]
    frame["target_unit"] = "celsius"
    frame.to_csv(features_path, index=False)

    output_path = tmp_path / "predictions.csv"
    rows = predict_gbm(features_path=features_path, model_path=model_path, output_path=output_path)

    assert output_path.exists()
    assert len(rows) == 24
    assert rows[0]["corrected_prediction_c"] is not None
    assert rows[0]["corrected_prediction_resolution_value"] is not None


def _write_training_fixture(path) -> None:
    fieldnames = [
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
    start = date(2026, 3, 1)
    rows = []
    for day_idx in range(12):
        current = start + timedelta(days=day_idx)
        base_max = 20 + day_idx * 0.2
        base_min = 10 + day_idx * 0.1
        for extreme in ("max", "min"):
            observed = base_max + 1.0 if extreme == "max" else base_min - 0.5
            rows.append(
                {
                    "station_id": "TEST",
                    "date": current.isoformat(),
                    "target_extreme": extreme,
                    "observed_temp_c": observed,
                    "station_latitude": 40.0,
                    "station_longitude": -70.0,
                    "station_elevation_m": 5.0,
                    "station_id_hash": 123,
                    "source_domain_hash": 0,
                    "target_day_of_year_sin": 0.1,
                    "target_day_of_year_cos": 0.9,
                    "target_month": current.month,
                    "target_is_weekend": int(current.weekday() >= 5),
                    "target_is_max": int(extreme == "max"),
                    "forecast_temp_max_c": base_max,
                    "forecast_temp_min_c": base_min,
                    "forecast_temp_mean_c": (base_max + base_min) / 2,
                    "forecast_temp_spread_c": base_max - base_min,
                    "forecast_hourly_count": 24,
                }
            )

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
