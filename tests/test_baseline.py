from __future__ import annotations

from detect_temperature.models.baseline import ExtremeTemperatureBaseline


def test_baseline_uses_matching_daily_extreme() -> None:
    model = ExtremeTemperatureBaseline()

    assert model.predict_one({"target_extreme": "max", "forecast_temp_max_c": "31.2"}) == 31.2
    assert model.predict_one({"target_extreme": "min", "forecast_temp_min_c": "18.4"}) == 18.4
    assert model.predict_one({"target_extreme": "unknown"}) is None

