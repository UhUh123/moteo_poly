from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from detect_temperature.polymarket import flatten_temperature_markets
from detect_temperature.risk_guards import DrawdownAbort, check_drawdown
from detect_temperature.schema import MarketTarget
from detect_temperature.signals import (
    build_market_signal,
    load_station_calibrations,
    sigma_for_station,
    SIGMA_FLOOR_C,
    SIGMA_MAE_MULTIPLIER,
)
from detect_temperature.sources.base import StationMetadata
from detect_temperature.station_verifier import verify_target


def _signal_fixture() -> dict:
    event = {
        "id": "1",
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "title": "Highest temperature in Test on May 5?",
        "volume": 1000,
        "markets": [
            {
                "id": "11",
                "conditionId": "0xabc",
                "slug": "highest-temperature-in-test-on-may-5-2026-17c",
                "question": "Will the highest temperature in Test be 17°C on May 5?",
                "groupItemTitle": "17°C",
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["0.4", "0.6"],
                "clobTokenIds": ["yes-token", "no-token"],
                "bestBid": 0.39,
                "bestAsk": 0.41,
                "spread": 0.02,
                "volume": "100",
                "liquidity": "1000",
                "negRisk": False,
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "endDate": "2099-05-05T12:00:00Z",
            }
        ],
    }
    return flatten_temperature_markets([event])[0].to_record()


def test_allow_buy_yes_false_disables_buy_yes() -> None:
    market_row = _signal_fixture()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "model_name": "test-model",
    }

    enabled = build_market_signal(
        market=market_row,
        prediction=prediction,
        sigma_c=1.0,
        min_edge=0.03,
        allow_buy_yes=True,
    )
    assert enabled["paper_side"] == "BUY_YES"

    disabled = build_market_signal(
        market=market_row,
        prediction=prediction,
        sigma_c=1.0,
        min_edge=0.03,
        allow_buy_yes=False,
    )
    assert disabled["paper_side"] == "NO_TRADE"
    assert "BUY_YES disabled" in disabled["reason"]


def test_station_verified_zero_forces_no_trade() -> None:
    market_row = _signal_fixture()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "model_name": "test-model",
        "station_verified": "0",
        "station_verification_reason": "missing station_id",
    }
    signal = build_market_signal(market=market_row, prediction=prediction, sigma_c=1.0, min_edge=0.03)
    assert signal["paper_side"] == "NO_TRADE"
    assert "station not verified" in signal["reason"]


def test_sigma_override_widens_probability_distribution() -> None:
    market_row = _signal_fixture()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "15.0",
        "model_name": "test-model",
    }
    narrow = build_market_signal(market=market_row, prediction=prediction, sigma_c=1.0, min_edge=0.01)
    wide = build_market_signal(market=market_row, prediction=prediction, sigma_c=2.5, min_edge=0.01)
    assert narrow["fair_yes_probability"] < wide["fair_yes_probability"]


def test_drawdown_kill_switch_breaches_and_passes(tmp_path: Path) -> None:
    breaching = tmp_path / "settled.json"
    breaching.write_text(json.dumps({"summary": {"realized_pnl_usdc": -12.5}}), encoding="utf-8")
    with pytest.raises(DrawdownAbort):
        check_drawdown(state_paths=[breaching], abort_usdc=-10.0)

    safe = tmp_path / "safe.json"
    safe.write_text(json.dumps({"summary": {"realized_pnl_usdc": -3.0}}), encoding="utf-8")
    result = check_drawdown(state_paths=[safe], abort_usdc=-10.0)
    assert result["breached"] is False
    assert result["realized_pnl_usdc"] == -3.0

    missing = tmp_path / "missing.json"
    result = check_drawdown(state_paths=[missing], abort_usdc=-10.0)
    assert result["breached"] is False
    assert result["realized_pnl_usdc"] == 0.0


def test_station_verifier_requires_station_and_domain() -> None:
    target = MarketTarget(
        title="t",
        slug="s",
        city="x",
        location_name="",
        target_date=date(2026, 5, 5),
        target_extreme="max",
        target_unit="celsius",
        station_id="",
        resolution_source_url="",
        source_domain="",
        description="",
    )
    ok, reason = verify_target(target)
    assert ok is False and "missing station_id" in reason

    target = MarketTarget(
        title="t",
        slug="s",
        city="x",
        location_name="",
        target_date=date(2026, 5, 5),
        target_extreme="max",
        target_unit="celsius",
        station_id="KSFO",
        resolution_source_url="https://www.wunderground.com/weather/KSFO",
        source_domain="wunderground.com",
        description="resolves in degrees Fahrenheit",
    )
    station = StationMetadata(station_id="KSFO", latitude=37.6, longitude=-122.4, country="US")
    ok, reason = verify_target(target, station)
    assert ok is True

    mismatched_station = StationMetadata(station_id="KJFK", latitude=40.6, longitude=-73.8, country="US")
    ok, reason = verify_target(target, mismatched_station)
    assert ok is False and "disagrees" in reason


def test_station_verifier_rejects_unsupported_domain() -> None:
    target = MarketTarget(
        title="t",
        slug="s",
        city="x",
        location_name="",
        target_date=date(2026, 5, 5),
        target_extreme="max",
        target_unit="celsius",
        station_id="XXXX",
        resolution_source_url="https://example.com/weather",
        source_domain="example.com",
        description="",
    )
    ok, reason = verify_target(target)
    assert ok is False and "unsupported source domain" in reason


def test_sigma_for_station_applies_floor_and_multiplier() -> None:
    calibrations = {"KSEA": 1.5, "WMKK": 0.01}  # noisy Seattle, tropical Kuala Lumpur
    # Seattle: 1.5 * 1.5 = 2.25 > floor 1.5
    assert sigma_for_station("KSEA", calibrations, default_sigma_c=2.5) == pytest.approx(2.25)
    # WMKK: 0.01 * 1.5 = 0.015 -> floored to 1.5
    assert sigma_for_station("WMKK", calibrations, default_sigma_c=2.5) == pytest.approx(SIGMA_FLOOR_C)
    # Unknown station falls back to default
    assert sigma_for_station("UNKNOWN", calibrations, default_sigma_c=2.5) == 2.5
    # No calibrations -> default
    assert sigma_for_station("KSEA", {}, default_sigma_c=2.5) == 2.5
    assert sigma_for_station(None, calibrations, default_sigma_c=2.5) == 2.5


def test_load_station_calibrations_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cal.csv"
    path.write_text(
        "station_id,rolling_mae_c,rolling_bias_c\nKSFO,0.8,-0.2\nEGLC,1.1,0.05\n",
        encoding="utf-8",
    )
    loaded = load_station_calibrations(path)
    assert loaded == {"KSFO": 0.8, "EGLC": 1.1}
    # missing file returns empty dict
    assert load_station_calibrations(tmp_path / "nope.csv") == {}


def test_build_market_signal_uses_station_sigma() -> None:
    market_row = _signal_fixture()
    market_row["best_ask"] = "0.30"
    market_row["best_bid"] = "0.28"
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "station_id": "NOISY",
        "model_name": "test-model",
    }

    tight = build_market_signal(market=market_row, prediction=prediction, sigma_c=1.0, min_edge=0.01)
    wide = build_market_signal(
        market=market_row,
        prediction=prediction,
        sigma_c=1.0,
        min_edge=0.01,
        station_calibrations={"NOISY": 2.5},  # 2.5 * 1.5 = 3.75 effective
    )
    # Wider sigma smears probability mass away from the bucket -> lower fair_yes.
    assert wide["fair_yes_probability"] < tight["fair_yes_probability"]
    assert wide["model_sigma_c"] == pytest.approx(2.5 * SIGMA_MAE_MULTIPLIER)
    assert wide["model_sigma_source"] == "station_calibration"
    assert tight["model_sigma_source"] == "default"
