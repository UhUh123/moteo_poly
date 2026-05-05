from __future__ import annotations

import csv
import json

from detect_temperature.paper import open_paper_portfolio, open_strategy_paper_portfolio, settle_paper_portfolio


def test_open_paper_portfolio_respects_budget_and_writes_dashboard(tmp_path) -> None:
    signals_path = tmp_path / "signals.csv"
    _write_signals(signals_path)
    output_path = tmp_path / "portfolio.csv"
    state_path = tmp_path / "portfolio.json"
    dashboard_path = tmp_path / "dashboard.html"

    payload = open_paper_portfolio(
        signals_path=signals_path,
        output_path=output_path,
        state_path=state_path,
        dashboard_path=dashboard_path,
        bankroll_usdc=100.0,
        max_positions=2,
        max_stake_usdc=5.0,
        max_total_exposure_pct=0.1,
        min_price=0.01,
    )

    assert output_path.exists()
    assert state_path.exists()
    assert dashboard_path.exists()
    assert payload["summary"]["positions"] == 2
    assert payload["summary"]["total_staked_usdc"] <= 10.0
    assert "Paper Weather Desk" in dashboard_path.read_text(encoding="utf-8")


def test_settle_paper_portfolio_marks_wins_and_losses(tmp_path) -> None:
    signals_path = tmp_path / "signals.csv"
    _write_signals(signals_path)
    portfolio_path = tmp_path / "portfolio.csv"
    open_paper_portfolio(
        signals_path=signals_path,
        output_path=portfolio_path,
        bankroll_usdc=100.0,
        max_positions=2,
        max_stake_usdc=5.0,
        max_total_exposure_pct=0.2,
        min_price=0.01,
    )

    actuals_path = tmp_path / "actuals.csv"
    _write_actuals(actuals_path)
    settled_path = tmp_path / "settled.csv"
    state_path = tmp_path / "settled.json"

    payload = settle_paper_portfolio(
        portfolio_path=portfolio_path,
        actuals_path=actuals_path,
        output_path=settled_path,
        state_path=state_path,
        bankroll_usdc=100.0,
    )

    rows = list(csv.DictReader(settled_path.open(newline="", encoding="utf-8")))
    assert {row["status"] for row in rows} == {"won", "lost"}
    assert payload["summary"]["settled_positions"] == 2
    assert payload["summary"]["won_positions"] == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["summary"]["lost_positions"] == 1


def test_open_strategy_paper_portfolio_tracks_taker_and_maker_modes(tmp_path) -> None:
    strategy_path = tmp_path / "strategy.csv"
    _write_strategy_portfolio(strategy_path)
    output_path = tmp_path / "portfolio.csv"
    dashboard_path = tmp_path / "dashboard.html"

    payload = open_strategy_paper_portfolio(
        strategy_portfolio_path=strategy_path,
        output_path=output_path,
        dashboard_path=dashboard_path,
        bankroll_usdc=100.0,
        execution_mode="maker-preferred",
    )

    rows = list(csv.DictReader(output_path.open(newline="", encoding="utf-8")))
    assert payload["summary"]["positions"] == 2
    assert payload["summary"]["maker_positions"] == 1
    assert payload["summary"]["taker_positions"] == 1
    assert payload["summary"]["maker_preferred_positions"] == 1
    assert payload["summary"]["expected_total_pnl_usdc"] == 7.0
    assert rows[0]["entry_mode"] == "maker"
    assert rows[0]["price"] == "0.15"
    assert rows[1]["entry_mode"] == "taker"
    assert rows[1]["price"] == "0.31"
    assert "Entry Mix" in dashboard_path.read_text(encoding="utf-8")


def _write_signals(path) -> None:
    fieldnames = [
        "event_slug",
        "event_title",
        "market_slug",
        "question",
        "group_item_title",
        "paper_side",
        "paper_price",
        "paper_fair_probability",
        "paper_net_edge",
        "suggested_max_stake_usdc",
        "yes_fee_per_share",
        "no_fee_per_share",
        "prediction_c",
        "interval_lower",
        "interval_upper",
        "interval_unit",
        "end_date",
        "market_has_ended",
    ]
    rows = [
        {
            "event_slug": "highest-temperature-in-test-on-may-5-2026",
            "event_title": "Highest temperature in Test on May 5?",
            "market_slug": "test-17c",
            "question": "Will the highest temperature in Test be 17C?",
            "group_item_title": "17C",
            "paper_side": "BUY_YES",
            "paper_price": "0.2",
            "paper_fair_probability": "0.6",
            "paper_net_edge": "0.39",
            "suggested_max_stake_usdc": "5",
            "yes_fee_per_share": "0.008",
            "no_fee_per_share": "0.008",
            "prediction_c": "17",
            "interval_lower": "16.5",
            "interval_upper": "17.5",
            "interval_unit": "celsius",
            "end_date": "2099-05-05T12:00:00Z",
            "market_has_ended": "0",
        },
        {
            "event_slug": "highest-temperature-in-test-on-may-6-2026",
            "event_title": "Highest temperature in Test on May 6?",
            "market_slug": "test-18c",
            "question": "Will the highest temperature in Test be 18C?",
            "group_item_title": "18C",
            "paper_side": "BUY_NO",
            "paper_price": "0.3",
            "paper_fair_probability": "0.7",
            "paper_net_edge": "0.38",
            "suggested_max_stake_usdc": "5",
            "yes_fee_per_share": "0.01",
            "no_fee_per_share": "0.01",
            "prediction_c": "17",
            "interval_lower": "17.5",
            "interval_upper": "18.5",
            "interval_unit": "celsius",
            "end_date": "2099-05-06T12:00:00Z",
            "market_has_ended": "0",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_strategy_portfolio(path) -> None:
    fieldnames = [
        "candidate_id",
        "selected",
        "event_slug",
        "event_title",
        "market_slug",
        "group_item_title",
        "side",
        "stake_usdc",
        "price",
        "execution_price",
        "execution_expected_roi",
        "execution_base_edge",
        "execution_quality",
        "base_fair_probability",
        "maker_preferred",
        "maker_quote_price",
        "maker_fill_score",
        "maker_fill_adjusted_expected_roi",
        "maker_edge_if_filled",
        "maker_reason",
        "robust_reason",
        "prediction_c",
        "interval_lower",
        "interval_upper",
        "interval_unit",
        "visible_top_bucket",
        "visible_bucket_rank",
        "visible_bucket_count",
        "decision_reason",
    ]
    rows = [
        {
            "candidate_id": "maker",
            "selected": "1",
            "event_slug": "highest-temperature-in-test-on-may-5-2026",
            "event_title": "Highest temperature in Test on May 5?",
            "market_slug": "test-17c",
            "group_item_title": "17C",
            "side": "BUY_YES",
            "stake_usdc": "4",
            "price": "0.2",
            "execution_price": "0.21",
            "execution_expected_roi": "0.5",
            "execution_base_edge": "0.28",
            "execution_quality": "fair",
            "base_fair_probability": "0.55",
            "maker_preferred": "1",
            "maker_quote_price": "0.15",
            "maker_fill_score": "0.5",
            "maker_fill_adjusted_expected_roi": "1.0",
            "maker_edge_if_filled": "0.39",
            "maker_reason": "maker preferred",
            "robust_reason": "passes all stress and execution checks",
            "prediction_c": "17",
            "interval_lower": "16.5",
            "interval_upper": "17.5",
            "interval_unit": "celsius",
            "visible_top_bucket": "17C",
            "visible_bucket_rank": "1",
            "visible_bucket_count": "5",
            "decision_reason": "test strategy maker",
        },
        {
            "candidate_id": "taker",
            "selected": "1",
            "event_slug": "highest-temperature-in-test-on-may-6-2026",
            "event_title": "Highest temperature in Test on May 6?",
            "market_slug": "test-18c",
            "group_item_title": "18C",
            "side": "BUY_NO",
            "stake_usdc": "6",
            "price": "0.3",
            "execution_price": "0.31",
            "execution_expected_roi": "0.5",
            "execution_base_edge": "0.35",
            "execution_quality": "good",
            "base_fair_probability": "0.7",
            "maker_preferred": "0",
            "maker_quote_price": "0.28",
            "maker_fill_score": "0.4",
            "maker_fill_adjusted_expected_roi": "0.2",
            "maker_edge_if_filled": "0.4",
            "maker_reason": "maker viable, taker expected value is higher",
            "robust_reason": "passes all stress and execution checks",
            "prediction_c": "17",
            "interval_lower": "17.5",
            "interval_upper": "18.5",
            "interval_unit": "celsius",
            "visible_top_bucket": "17C",
            "visible_bucket_rank": "2",
            "visible_bucket_count": "5",
            "decision_reason": "test strategy taker",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_actuals(path) -> None:
    fieldnames = ["slug", "status", "observed_temp_c"]
    rows = [
        {"slug": "highest-temperature-in-test-on-may-5-2026", "status": "ok", "observed_temp_c": "17.0"},
        {"slug": "highest-temperature-in-test-on-may-6-2026", "status": "ok", "observed_temp_c": "18.0"},
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
