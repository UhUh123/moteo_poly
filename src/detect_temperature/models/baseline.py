from __future__ import annotations

from typing import Any


class ExtremeTemperatureBaseline:
    model_name = "nwp_daily_extreme_baseline"

    def predict_one(self, row: dict[str, Any]) -> float | None:
        target_extreme = str(row.get("target_extreme") or "").lower()
        if target_extreme == "max":
            return _as_float(row.get("forecast_temp_max_c"))
        if target_extreme == "min":
            return _as_float(row.get("forecast_temp_min_c"))
        return None


def _as_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

