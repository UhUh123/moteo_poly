from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

DEFAULT_FEATURE_COLUMNS = [
    "target_day_of_year_sin",
    "target_day_of_year_cos",
    "target_month",
    "target_is_weekend",
    "target_is_max",
    "station_latitude",
    "station_longitude",
    "station_elevation_m",
    "station_id_hash",
    "source_domain_hash",
    "forecast_temp_max_c",
    "forecast_temp_min_c",
    "forecast_temp_mean_c",
    "forecast_temp_spread_c",
    "latest_observation_temp_c",
    "latest_observation_dewpoint_c",
    "latest_observation_pressure_hpa",
    "latest_observation_wind_speed_mps",
    "observation_age_hours",
]


def select_available_feature_columns(
    frame: pd.DataFrame,
    candidate_columns: list[str] | None = None,
) -> list[str]:
    columns = []
    for column in candidate_columns or DEFAULT_FEATURE_COLUMNS:
        if column not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().any():
            columns.append(column)
    if not columns:
        raise ValueError("No usable numeric feature columns found")
    return columns


class BiasCorrectedGBM:
    model_name = "sklearn_hist_gradient_boosting_bias_corrector"

    def __init__(
        self,
        feature_columns: list[str] | None = None,
        random_state: int = 42,
    ) -> None:
        self.feature_columns = feature_columns or DEFAULT_FEATURE_COLUMNS
        self.pipeline = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "regressor",
                    HistGradientBoostingRegressor(
                        loss="squared_error",
                        learning_rate=0.05,
                        max_iter=300,
                        l2_regularization=0.05,
                        random_state=random_state,
                    ),
                ),
            ]
        )

    def fit(self, frame: pd.DataFrame, target_column: str = "observed_temp_c") -> "BiasCorrectedGBM":
        missing = [column for column in self.feature_columns if column not in frame.columns]
        if missing:
            raise ValueError(f"Missing feature columns: {missing}")
        if target_column not in frame.columns:
            raise ValueError(f"Missing target column: {target_column}")

        train = frame.dropna(subset=[target_column]).copy()
        if train.empty:
            raise ValueError(f"No rows with {target_column!r} labels")

        y = pd.to_numeric(train[target_column], errors="coerce")
        baseline = _baseline_series(train)
        residual = y - baseline
        valid = residual.notna()
        if not valid.any():
            raise ValueError("No rows with both labels and baseline forecast")

        x = _numeric_frame(train, self.feature_columns)
        self.pipeline.fit(x.loc[valid], residual.loc[valid])
        return self

    def predict(self, frame: pd.DataFrame) -> pd.Series:
        missing = [column for column in self.feature_columns if column not in frame.columns]
        if missing:
            raise ValueError(f"Missing feature columns: {missing}")
        x = _numeric_frame(frame, self.feature_columns)
        residual = pd.Series(self.pipeline.predict(x), index=frame.index)
        return (_baseline_series(frame) + residual).rename("corrected_prediction_c")

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "feature_columns": self.feature_columns,
                "pipeline": self.pipeline,
                "model_name": self.model_name,
            },
            path,
        )

    @classmethod
    def load(cls, path: str | Path) -> "BiasCorrectedGBM":
        payload = joblib.load(path)
        model = cls(feature_columns=payload["feature_columns"])
        model.pipeline = payload["pipeline"]
        return model


def _numeric_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")


def _baseline_series(frame: pd.DataFrame) -> pd.Series:
    max_values = pd.to_numeric(frame.get("forecast_temp_max_c"), errors="coerce")
    min_values = pd.to_numeric(frame.get("forecast_temp_min_c"), errors="coerce")
    target_extreme = frame.get("target_extreme", pd.Series("", index=frame.index)).astype(str).str.lower()
    return max_values.where(target_extreme.eq("max"), min_values)
