from __future__ import annotations

import csv
import json

from detect_temperature.strategy_lab import run_strategy_lab


def test_strategy_lab_filters_unstable_edges_and_selects_robust_portfolio(tmp_path) -> None:
    signals_path = tmp_path / "signals.csv"
    candidates_path = tmp_path / "candidates.csv"
    portfolio_path = tmp_path / "portfolio.csv"
    summary_path = tmp_path / "summary.json"
    report_path = tmp_path / "report.html"
    _write_signals(signals_path)

    payload = run_strategy_lab(
        signals_path=signals_path,
        candidates_output_path=candidates_path,
        portfolio_output_path=portfolio_path,
        summary_output_path=summary_path,
        report_path=report_path,
        bankroll_usdc=100.0,
        max_positions=5,
        max_stake_usdc=2.0,
        robust_min_edge=0.01,
    )

    candidates = list(csv.DictReader(candidates_path.open(newline="", encoding="utf-8")))
    selected = list(csv.DictReader(portfolio_path.open(newline="", encoding="utf-8")))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))["summary"]

    assert report_path.exists()
    assert len(candidates) == 2
    assert summary["robust_pass"] == 1
    assert payload["summary"]["selected_positions"] == 1
    assert len(selected) == 1
    assert selected[0]["side"] == "BUY_NO"
    assert selected[0]["group_item_title"] == "25C"
    assert float(selected[0]["worst_edge"]) > 0.01
    assert selected[0]["maker_eligible"] == "1"
    assert float(selected[0]["maker_quote_price"]) < float(selected[0]["price"])
    assert float(selected[0]["maker_fill_score"]) >= 0.35
    assert any(row["group_item_title"] == "20C" and row["robust_pass"] == "0" for row in candidates)


def test_strategy_lab_enforces_city_concentration_caps(tmp_path) -> None:
    signals_path = tmp_path / "signals.csv"
    candidates_path = tmp_path / "candidates.csv"
    portfolio_path = tmp_path / "portfolio.csv"
    _write_city_cap_signals(signals_path)

    run_strategy_lab(
        signals_path=signals_path,
        candidates_output_path=candidates_path,
        portfolio_output_path=portfolio_path,
        bankroll_usdc=100.0,
        max_positions=5,
        max_stake_usdc=2.0,
        robust_min_edge=0.01,
        max_city_positions=2,
    )

    selected = list(csv.DictReader(portfolio_path.open(newline="", encoding="utf-8")))
    assert len(selected) == 3
    assert sum(row["market_city"] == "Test" for row in selected) == 2
    assert any(row["market_city"] == "Other" for row in selected)


def test_strategy_lab_rejects_excessive_execution_slippage(tmp_path) -> None:
    signals_path = tmp_path / "signals.csv"
    candidates_path = tmp_path / "candidates.csv"
    _write_execution_slippage_signals(signals_path)

    payload = run_strategy_lab(
        signals_path=signals_path,
        candidates_output_path=candidates_path,
        bankroll_usdc=100.0,
        max_positions=5,
        max_stake_usdc=2.0,
        robust_min_edge=0.01,
        max_execution_slippage=0.03,
    )

    candidates = list(csv.DictReader(candidates_path.open(newline="", encoding="utf-8")))
    assert payload["summary"]["robust_pass"] == 0
    assert candidates[0]["execution_quality"] == "poor"
    assert float(candidates[0]["execution_slippage"]) > 0.03
    assert "execution slippage" in candidates[0]["robust_reason"]


def test_strategy_lab_rejects_when_orderbook_depth_cannot_fill_stake(tmp_path) -> None:
    signals_path = tmp_path / "signals.csv"
    candidates_path = tmp_path / "candidates.csv"
    orderbooks_path = tmp_path / "orderbooks.json"
    _write_orderbook_depth_signal(signals_path)
    orderbooks_path.write_text(
        json.dumps(
            {
                "books": [
                    {
                        "asset_id": "no-token",
                        "asks": [{"price": "0.20", "size": "1"}],
                        "bids": [{"price": "0.19", "size": "10"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = run_strategy_lab(
        signals_path=signals_path,
        candidates_output_path=candidates_path,
        orderbooks_path=orderbooks_path,
        bankroll_usdc=100.0,
        max_positions=5,
        max_stake_usdc=2.0,
        robust_min_edge=0.01,
    )

    candidates = list(csv.DictReader(candidates_path.open(newline="", encoding="utf-8")))
    assert payload["summary"]["orderbook_count"] == 1
    assert payload["summary"]["robust_pass"] == 0
    assert candidates[0]["execution_fillable"] == "0"
    assert float(candidates[0]["execution_fill_ratio"]) < 1.0
    assert "orderbook depth" in candidates[0]["robust_reason"]


def _write_signals(path) -> None:
    fieldnames = [
        "event_slug",
        "event_title",
        "market_slug",
        "group_item_title",
        "paper_side",
        "paper_price",
        "paper_fair_probability",
        "paper_net_edge",
        "prediction_c",
        "interval_lower",
        "interval_upper",
        "interval_unit",
        "market_has_ended",
        "suggested_max_stake_usdc",
        "best_bid",
        "best_ask",
        "spread",
        "liquidity",
        "market_volume",
        "visible_top_bucket",
        "visible_bucket_rank",
        "visible_bucket_count",
        "risk_flags",
        "decision_reason",
    ]
    rows = [
        {
            "event_slug": "highest-temperature-in-test-on-may-5-2026",
            "event_title": "Highest temperature in Test on May 5?",
            "market_slug": "test-20c",
            "group_item_title": "20C",
            "paper_side": "BUY_YES",
            "paper_price": "0.20",
            "paper_fair_probability": "0.38",
            "paper_net_edge": "0.17",
            "prediction_c": "20.0",
            "interval_lower": "19.5",
            "interval_upper": "20.5",
            "interval_unit": "celsius",
            "market_has_ended": "0",
            "suggested_max_stake_usdc": "2",
            "best_bid": "0.18",
            "best_ask": "0.20",
            "spread": "0.01",
            "liquidity": "1500",
            "market_volume": "5000",
            "visible_top_bucket": "20C",
            "visible_bucket_rank": "1",
            "visible_bucket_count": "5",
            "risk_flags": "",
            "decision_reason": "test unstable yes",
        },
        {
            "event_slug": "highest-temperature-in-test-on-may-5-2026",
            "event_title": "Highest temperature in Test on May 5?",
            "market_slug": "test-25c",
            "group_item_title": "25C",
            "paper_side": "BUY_NO",
            "paper_price": "0.20",
            "paper_fair_probability": "0.99",
            "paper_net_edge": "0.78",
            "prediction_c": "20.0",
            "interval_lower": "24.5",
            "interval_upper": "25.5",
            "interval_unit": "celsius",
            "market_has_ended": "0",
            "suggested_max_stake_usdc": "2",
            "best_bid": "0.80",
            "best_ask": "0.82",
            "spread": "0.01",
            "liquidity": "1500",
            "market_volume": "5000",
            "visible_top_bucket": "20C",
            "visible_bucket_rank": "5",
            "visible_bucket_count": "5",
            "risk_flags": "",
            "decision_reason": "test robust no",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_orderbook_depth_signal(path) -> None:
    fieldnames = [
        "event_slug",
        "event_title",
        "market_slug",
        "group_item_title",
        "paper_side",
        "paper_price",
        "paper_fair_probability",
        "paper_net_edge",
        "prediction_c",
        "interval_lower",
        "interval_upper",
        "interval_unit",
        "market_has_ended",
        "suggested_max_stake_usdc",
        "best_bid",
        "best_ask",
        "spread",
        "liquidity",
        "market_volume",
        "yes_token_id",
        "no_token_id",
        "visible_top_bucket",
        "visible_bucket_rank",
        "visible_bucket_count",
        "risk_flags",
        "decision_reason",
    ]
    rows = [
        {
            "event_slug": "highest-temperature-in-book-on-may-5-2026",
            "event_title": "Highest temperature in Book on May 5?",
            "market_slug": "book-25c",
            "group_item_title": "25C",
            "paper_side": "BUY_NO",
            "paper_price": "0.20",
            "paper_fair_probability": "0.99",
            "paper_net_edge": "0.78",
            "prediction_c": "20.0",
            "interval_lower": "24.5",
            "interval_upper": "25.5",
            "interval_unit": "celsius",
            "market_has_ended": "0",
            "suggested_max_stake_usdc": "2",
            "best_bid": "0.80",
            "best_ask": "0.82",
            "spread": "0.01",
            "liquidity": "1500",
            "market_volume": "5000",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "visible_top_bucket": "20C",
            "visible_bucket_rank": "5",
            "visible_bucket_count": "5",
            "risk_flags": "",
            "decision_reason": "test orderbook depth",
        }
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_city_cap_signals(path) -> None:
    fieldnames = [
        "event_slug",
        "event_title",
        "market_slug",
        "group_item_title",
        "paper_side",
        "paper_price",
        "paper_fair_probability",
        "paper_net_edge",
        "prediction_c",
        "interval_lower",
        "interval_upper",
        "interval_unit",
        "market_has_ended",
        "suggested_max_stake_usdc",
        "best_bid",
        "best_ask",
        "spread",
        "liquidity",
        "market_volume",
        "visible_top_bucket",
        "visible_bucket_rank",
        "visible_bucket_count",
        "risk_flags",
        "decision_reason",
    ]
    rows = []
    for index, city in enumerate(["Test", "Test", "Test", "Other"], start=1):
        rows.append(
            {
                "event_slug": f"highest-temperature-in-{city.lower()}-on-may-{index}-2026",
                "event_title": f"Highest temperature in {city} on May {index}?",
                "market_slug": f"{city.lower()}-{index}-25c",
                "group_item_title": "25C",
                "paper_side": "BUY_NO",
                "paper_price": "0.20",
                "paper_fair_probability": "0.99",
                "paper_net_edge": "0.78",
                "prediction_c": "20.0",
                "interval_lower": "24.5",
                "interval_upper": "25.5",
                "interval_unit": "celsius",
                "market_has_ended": "0",
                "suggested_max_stake_usdc": "2",
                "best_bid": "0.80",
                "best_ask": "0.82",
                "spread": "0.01",
                "liquidity": "1500",
                "market_volume": "5000",
                "visible_top_bucket": "20C",
                "visible_bucket_rank": "5",
                "visible_bucket_count": "5",
                "risk_flags": "",
                "decision_reason": "test city cap",
            }
        )
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_execution_slippage_signals(path) -> None:
    fieldnames = [
        "event_slug",
        "event_title",
        "market_slug",
        "group_item_title",
        "paper_side",
        "paper_price",
        "paper_fair_probability",
        "paper_net_edge",
        "prediction_c",
        "interval_lower",
        "interval_upper",
        "interval_unit",
        "market_has_ended",
        "suggested_max_stake_usdc",
        "best_bid",
        "best_ask",
        "spread",
        "liquidity",
        "market_volume",
        "visible_top_bucket",
        "visible_bucket_rank",
        "visible_bucket_count",
        "risk_flags",
        "decision_reason",
    ]
    rows = [
        {
            "event_slug": "highest-temperature-in-thin-on-may-5-2026",
            "event_title": "Highest temperature in Thin on May 5?",
            "market_slug": "thin-25c",
            "group_item_title": "25C",
            "paper_side": "BUY_NO",
            "paper_price": "0.20",
            "paper_fair_probability": "0.99",
            "paper_net_edge": "0.78",
            "prediction_c": "20.0",
            "interval_lower": "24.5",
            "interval_upper": "25.5",
            "interval_unit": "celsius",
            "market_has_ended": "0",
            "suggested_max_stake_usdc": "2",
            "best_bid": "0.80",
            "best_ask": "0.88",
            "spread": "0.08",
            "liquidity": "20",
            "market_volume": "50",
            "visible_top_bucket": "20C",
            "visible_bucket_rank": "5",
            "visible_bucket_count": "5",
            "risk_flags": "",
            "decision_reason": "test poor execution",
        }
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
