from __future__ import annotations

import csv
import json

import pytest

from detect_temperature.polymarket import extract_weather_events_from_html, flatten_temperature_markets
from detect_temperature.signals import (
    build_market_signal,
    build_market_signals,
    kelly_fraction,
    normal_interval_probability,
    parse_temperature_interval,
)


def test_extract_weather_events_from_html() -> None:
    payload = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {"queryKey": ["weather", "markets"], "state": {"data": [_event_fixture()]}}
                    ]
                }
            }
        }
    }
    page = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></html>'

    events = extract_weather_events_from_html(page)
    markets = flatten_temperature_markets(events)

    assert len(events) == 1
    assert len(markets) == 1
    assert markets[0].event_slug == "highest-temperature-in-test-on-may-5-2026"
    assert markets[0].outcome_prices == [0.4, 0.6]


@pytest.mark.parametrize(
    ("question", "lower", "upper", "unit"),
    [
        ("Will the highest temperature in Chicago be between 58-59°F on May 5?", 58.0, 60.0, "fahrenheit"),
        ("Will the highest temperature in London be 17°C on May 5?", 16.5, 17.5, "celsius"),
        ("Will the highest temperature in Tokyo be 23°C or higher on May 5?", 22.5, None, "celsius"),
        ("Will the lowest temperature in Lucknow be 33°C or below on May 5?", None, 33.5, "celsius"),
    ],
)
def test_parse_temperature_interval(question: str, lower: float | None, upper: float | None, unit: str) -> None:
    interval = parse_temperature_interval(question)
    assert interval is not None
    assert interval.lower == lower
    assert interval.upper == upper
    assert interval.unit == unit


def test_normal_interval_probability() -> None:
    probability = normal_interval_probability(mean=20.0, sigma=1.0, lower=19.5, upper=20.5)
    assert probability == pytest.approx(0.3829, abs=0.001)


def test_build_market_signal_recommends_buy_yes() -> None:
    market = _event_fixture()["markets"][0]
    market_row = flatten_temperature_markets([_event_fixture()])[0].to_record()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "model_name": "test-model",
    }

    signal = build_market_signal(market=market_row, prediction=prediction, sigma_c=1.0, min_edge=0.03)

    assert market["question"] == market_row["question"]
    assert signal["paper_side"] == "BUY_YES"
    assert signal["fair_yes_probability"] > 0.3
    assert signal["yes_net_edge"] > 0.03


def test_build_market_signals_blocks_no_against_model_top_bucket(tmp_path) -> None:
    event = _event_fixture()
    event["markets"].append(
        {
            **event["markets"][0],
            "id": "12",
            "slug": "highest-temperature-in-test-on-may-5-2026-18c",
            "question": "Will the highest temperature in Test be 18°C on May 5?",
            "groupItemTitle": "18°C",
            "bestBid": 0.2,
            "bestAsk": 0.21,
            "outcomePrices": ["0.2", "0.8"],
        }
    )
    market_rows = [market.to_record() for market in flatten_temperature_markets([event])]
    market_rows[0]["best_ask"] = "0.99"
    market_rows[0]["best_bid"] = "0.99"
    market_rows[0]["yes_price"] = "0.99"
    market_rows[0]["no_price"] = "0.01"
    markets_path = tmp_path / "markets.csv"
    predictions_path = tmp_path / "predictions.csv"
    output_path = tmp_path / "signals.csv"
    _write_rows(markets_path, market_rows)
    _write_rows(
        predictions_path,
        [
            {
                "slug": "highest-temperature-in-test-on-may-5-2026",
                "corrected_prediction_c": "17.0",
                "model_name": "test-model",
            }
        ],
    )

    rows = build_market_signals(
        markets_path=markets_path,
        predictions_path=predictions_path,
        output_path=output_path,
        sigma_c=1.0,
        min_edge=0.03,
    )

    top_row = next(row for row in rows if row["group_item_title"] == "17°C")
    assert top_row["is_visible_top_bucket"] == 1
    assert top_row["paper_side"] == "NO_TRADE"
    assert "model top bucket" in top_row["reason"]


def _event_fixture() -> dict:
    return {
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
                "liquidity": "50",
                "negRisk": False,
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "endDate": "2099-05-05T12:00:00Z",
            }
        ],
    }


def _write_rows(path, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_kelly_fraction_chapter4_examples() -> None:
    # Seoul example from chapter 4 §5: p=0.38, q=0.42, edge=0.04
    # f* = 0.04 / (1 - 0.38) = 0.0645... (chapter quotes ~0.065)
    assert kelly_fraction(0.04, 0.38) == pytest.approx(0.06451, abs=1e-4)
    # Cheap tail from §6: p=0.10, q=0.20, edge=0.10 -> f* = 0.111
    assert kelly_fraction(0.10, 0.10) == pytest.approx(0.11111, abs=1e-4)
    # No edge -> 0, do not bet
    assert kelly_fraction(0.0, 0.5) == 0.0
    # Negative edge -> negative fraction (caller must reject)
    assert kelly_fraction(-0.05, 0.5) == pytest.approx(-0.10, abs=1e-9)
    # Boundary cases must not blow up
    assert kelly_fraction(0.05, 1.0) is None
    assert kelly_fraction(None, 0.5) is None
    assert kelly_fraction(0.05, None) is None


def test_build_market_signal_emits_kelly_columns() -> None:
    market_row = flatten_temperature_markets([_event_fixture()])[0].to_record()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "model_name": "test-model",
    }

    signal = build_market_signal(market=market_row, prediction=prediction, sigma_c=1.0, min_edge=0.03)

    assert "yes_kelly_fraction" in signal
    assert "no_kelly_fraction" in signal
    # YES side has positive edge in this fixture, so Kelly must be a positive number
    assert signal["yes_kelly_fraction"] is not None
    assert signal["yes_kelly_fraction"] > 0


def test_build_market_signal_carries_identity_columns_from_prediction() -> None:
    """Regression for the live data-quality bug where 90 paper positions
    landed on disk with empty target_date / station_id / target_extreme /
    city columns. These fields exist in the prediction row (originating
    from targets.csv) but used to be dropped at the signals stage because
    `base = dict(market)` only carried Polymarket fields. Without them
    _stuck_paper_targets cannot reconstruct a MarketTarget for a stuck
    paper position whose slug rotated out of targets.csv."""
    market_row = flatten_temperature_markets([_event_fixture()])[0].to_record()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "model_name": "test-model",
        "target_date": "2026-05-05",
        "station_id": "KSFO",
        "target_extreme": "max",
        "city": "Test",
        "target_unit": "celsius",
        "location_name": "Test Airport",
        "source_domain": "wunderground.com",
    }

    signal = build_market_signal(market=market_row, prediction=prediction, sigma_c=1.0, min_edge=0.03)

    assert signal.get("target_date") == "2026-05-05"
    assert signal.get("station_id") == "KSFO"
    assert signal.get("target_extreme") == "max"
    assert signal.get("city") == "Test"
    assert signal.get("target_unit") == "celsius"
    assert signal.get("location_name") == "Test Airport"
    assert signal.get("source_domain") == "wunderground.com"


def test_build_market_signal_does_not_overwrite_market_provided_columns() -> None:
    """If the market row already happens to carry one of these columns
    (rare but possible in future feeds), the existing value wins. We
    must not silently rewrite Polymarket-provided data with prediction
    metadata."""
    market_row = flatten_temperature_markets([_event_fixture()])[0].to_record()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    market_row["target_date"] = "2099-01-01"  # synthetic, deliberately wrong
    prediction = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "model_name": "test-model",
        "target_date": "2026-05-05",
        "station_id": "KSFO",
    }

    signal = build_market_signal(market=market_row, prediction=prediction, sigma_c=1.0, min_edge=0.03)

    assert signal["target_date"] == "2099-01-01", "market value must win when both sides have it"
    assert signal["station_id"] == "KSFO", "missing market value falls back to prediction"


def test_build_market_signal_handles_missing_prediction_metadata() -> None:
    """Older prediction rows (pre-fix backfill) lack the new columns.
    Signal must still be constructed with empty strings, not crash."""
    market_row = flatten_temperature_markets([_event_fixture()])[0].to_record()
    market_row["best_ask"] = "0.20"
    market_row["best_bid"] = "0.18"
    prediction_old_schema = {
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "corrected_prediction_c": "17.0",
        "model_name": "test-model",
    }

    signal = build_market_signal(
        market=market_row, prediction=prediction_old_schema, sigma_c=1.0, min_edge=0.03
    )

    assert signal.get("target_date", "") == ""
    assert signal.get("station_id", "") == ""
