from __future__ import annotations

import csv
import json

from detect_temperature.paper import (
    OPEN_STATUSES,
    SETTLED_STATUSES,
    open_paper_portfolio,
    open_strategy_paper_portfolio,
    settle_paper_portfolio,
)


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
        cross_check_polymarket=False,
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


def test_open_strategy_paper_portfolio_preserves_prior_open_positions(tmp_path) -> None:
    strategy_path = tmp_path / "strategy.csv"
    _write_strategy_portfolio(strategy_path)
    output_path = tmp_path / "portfolio.csv"

    first = open_strategy_paper_portfolio(
        strategy_portfolio_path=strategy_path,
        output_path=output_path,
        bankroll_usdc=100.0,
    )
    assert first["summary"]["positions"] == 2
    assert first["summary"]["carried_positions"] == 0
    assert first["summary"]["new_positions_added"] == 2

    # Simulate one of the two positions getting settled overnight
    import csv as _csv
    rows = list(_csv.DictReader(output_path.open(newline="", encoding="utf-8")))
    rows[0]["status"] = "won"
    rows[0]["won"] = "1"
    rows[0]["pnl_usdc"] = "3.5"
    fieldnames = list(rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    # Second run with the SAME strategy portfolio: should NOT re-open the same
    # (event, side) positions, and must keep the settled history.
    second = open_strategy_paper_portfolio(
        strategy_portfolio_path=strategy_path,
        output_path=output_path,
        bankroll_usdc=100.0,
    )
    assert second["summary"]["carried_positions"] == 2
    assert second["summary"]["new_positions_added"] == 0
    assert second["summary"]["positions"] == 2

    final_rows = list(_csv.DictReader(output_path.open(newline="", encoding="utf-8")))
    statuses = {row["status"] for row in final_rows}
    assert "won" in statuses  # settled row preserved
    assert sum(1 for r in final_rows if r["status"] == "open") == 1  # the other stayed open


def test_open_strategy_paper_portfolio_preserve_open_false_restores_old_behaviour(tmp_path) -> None:
    strategy_path = tmp_path / "strategy.csv"
    _write_strategy_portfolio(strategy_path)
    output_path = tmp_path / "portfolio.csv"
    open_strategy_paper_portfolio(
        strategy_portfolio_path=strategy_path,
        output_path=output_path,
        bankroll_usdc=100.0,
    )
    # With preserve_open=False the function should wipe and rewrite from scratch.
    payload = open_strategy_paper_portfolio(
        strategy_portfolio_path=strategy_path,
        output_path=output_path,
        bankroll_usdc=100.0,
        preserve_open=False,
    )
    assert payload["summary"]["carried_positions"] == 0
    assert payload["summary"]["new_positions_added"] == 2


def test_open_and_settled_status_constants() -> None:
    # Protect the invariant the pipeline relies on: at_risk is open, won/lost
    # are settled. The refresh_open_positions path writes these statuses.
    assert "at_risk" in OPEN_STATUSES
    assert "open" in OPEN_STATUSES
    assert "won" in SETTLED_STATUSES
    assert "lost" in SETTLED_STATUSES


# ---------- Polymarket cross-check tests --------------------------------------

import pytest


def _write_signals_for_cross_check(path) -> None:
    """A minimal signals CSV with one BUY_NO position we can later settle."""
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
    rows = [{
        "event_slug": "highest-temperature-in-test-on-may-5-2026",
        "event_title": "Highest temperature in Test on May 5?",
        "market_slug": "highest-temperature-in-test-on-may-5-2026-17c",
        "question": "Will the highest temperature in Test be 17°C on May 5?",
        "group_item_title": "17°C",
        "paper_side": "BUY_NO",
        "paper_price": "0.6",
        "paper_fair_probability": "0.7",
        "paper_net_edge": "0.05",
        "suggested_max_stake_usdc": "1",
        "yes_fee_per_share": "0.005",
        "no_fee_per_share": "0.005",
        "prediction_c": "20",
        "interval_lower": "16.5",
        "interval_upper": "17.5",
        "interval_unit": "celsius",
        "end_date": "2099-05-05T12:00:00Z",
        "market_has_ended": "0",
    }]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _write_actual_temp_outside_bucket(path) -> None:
    """Actual is 22°C, the bucket was 17°C. So our verdict: BUY_NO won."""
    rows = [{
        "slug": "highest-temperature-in-test-on-may-5-2026",
        "status": "ok",
        "observed_temp_c": "22.0",
    }]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["slug", "status", "observed_temp_c"])
        w.writeheader()
        w.writerows(rows)


def _open_one_position(tmp_path):
    """Helper: build a paper portfolio with exactly one BUY_NO position."""
    signals = tmp_path / "signals.csv"
    _write_signals_for_cross_check(signals)
    portfolio = tmp_path / "portfolio.csv"
    open_paper_portfolio(
        signals_path=signals,
        output_path=portfolio,
        bankroll_usdc=100.0,
        max_positions=1,
        max_stake_usdc=5.0,
        max_total_exposure_pct=0.2,
        min_price=0.01,
    )
    actuals = tmp_path / "actuals.csv"
    _write_actual_temp_outside_bucket(actuals)
    return portfolio, actuals


def test_settle_records_polymarket_agree_when_both_say_no_won(tmp_path, monkeypatch) -> None:
    """Both we and Polymarket think NO won -> settle_agreement='agree'."""
    portfolio, actuals = _open_one_position(tmp_path)

    from detect_temperature.polymarket_resolution import MarketResolution

    fake_resolution = MarketResolution(
        market_slug="highest-temperature-in-test-on-may-5-2026-17c",
        closed=True,
        yes_outcome_price=0.0,
        no_outcome_price=1.0,
        outcome_prices_raw='["0", "1"]',
        uma_status="resolved",
        resolution_source="https://wunderground.com/...",
    )

    def fake_fetch(event_slug):
        assert event_slug == "highest-temperature-in-test-on-may-5-2026"
        return {"highest-temperature-in-test-on-may-5-2026-17c": fake_resolution}

    monkeypatch.setattr(
        "detect_temperature.paper.fetch_event_resolution",
        fake_fetch,
        raising=False,
    )
    # We patch via the polymarket_resolution module so the import inside
    # _fetch_polymarket_resolutions resolves to fake_fetch. Two equally
    # valid call sites — patch both to avoid a second fetch attempt.
    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        fake_fetch,
    )

    settled_csv = tmp_path / "settled.csv"
    payload = settle_paper_portfolio(
        portfolio_path=portfolio,
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
    )

    rows = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "won"  # actual=22 outside [16.5,17.5] -> NO won
    assert row["polymarket_yes_won"] == "0"
    assert row["polymarket_uma_status"] == "resolved"
    assert row["settle_agreement"] == "agree"

    stats = payload["summary"]["polymarket_cross_check"]
    assert stats == {"agree": 1, "disagree": 0, "pending": 0, "no_data": 0}


def test_settle_records_polymarket_disagree(tmp_path, monkeypatch) -> None:
    """We say NO won (actual 22 > 17.5), Polymarket says YES won. The PnL
    must STILL follow our verdict — Polymarket is audit only."""
    portfolio, actuals = _open_one_position(tmp_path)

    from detect_temperature.polymarket_resolution import MarketResolution

    fake_resolution = MarketResolution(
        market_slug="highest-temperature-in-test-on-may-5-2026-17c",
        closed=True,
        yes_outcome_price=1.0,
        no_outcome_price=0.0,
        outcome_prices_raw='["1", "0"]',
        uma_status="resolved",
        resolution_source="https://wunderground.com/...",
    )

    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        lambda slug: {"highest-temperature-in-test-on-may-5-2026-17c": fake_resolution},
    )

    settled_csv = tmp_path / "settled.csv"
    payload = settle_paper_portfolio(
        portfolio_path=portfolio,
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
    )

    rows = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))
    row = rows[0]
    # Our verdict still wins: PnL is from won status, NOT from polymarket
    assert row["status"] == "won", "our PnL must come from actuals.csv, not from polymarket"
    assert float(row["pnl_usdc"]) > 0
    # But cross-check column flags the disagreement
    assert row["polymarket_yes_won"] == "1"
    assert row["settle_agreement"] == "disagree"

    stats = payload["summary"]["polymarket_cross_check"]
    assert stats["disagree"] == 1


def test_settle_handles_polymarket_fetch_failure_gracefully(tmp_path, monkeypatch) -> None:
    """Network blowup must not break settle — PnL still computes from
    actuals, and the cross-check column reports 'no_data'."""
    portfolio, actuals = _open_one_position(tmp_path)

    def boom(slug):
        raise RuntimeError("simulated network outage")

    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        boom,
    )

    settled_csv = tmp_path / "settled.csv"
    payload = settle_paper_portfolio(
        portfolio_path=portfolio,
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
    )

    rows = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))
    row = rows[0]
    assert row["status"] == "won"  # PnL still works
    assert row["settle_agreement"] == "no_data"
    assert payload["summary"]["polymarket_cross_check"]["no_data"] == 1


def test_settle_records_pending_when_market_not_resolved_yet(tmp_path, monkeypatch) -> None:
    """Polymarket sometimes returns closed=True but with mid-prices while
    UMA is still proposing. We must record 'pending', not invent a
    verdict on flickering numbers."""
    portfolio, actuals = _open_one_position(tmp_path)

    from detect_temperature.polymarket_resolution import MarketResolution

    in_progress = MarketResolution(
        market_slug="highest-temperature-in-test-on-may-5-2026-17c",
        closed=True,
        yes_outcome_price=0.5,
        no_outcome_price=0.5,
        outcome_prices_raw='["0.5", "0.5"]',
        uma_status="proposed",
        resolution_source="",
    )

    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        lambda slug: {"highest-temperature-in-test-on-may-5-2026-17c": in_progress},
    )

    settled_csv = tmp_path / "settled.csv"
    settle_paper_portfolio(
        portfolio_path=portfolio,
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
    )

    rows = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))
    row = rows[0]
    # Our PnL is final — we don't wait for polymarket
    assert row["status"] == "won"
    # But the cross-check explicitly marks the market as not yet final
    assert row["settle_agreement"] == "pending"
    assert row["polymarket_uma_status"] == "proposed"


def test_settle_cross_check_disabled_skips_network_completely(tmp_path, monkeypatch) -> None:
    """cross_check_polymarket=False must NOT make any network calls.
    This is the path used by tests and any environment where the
    Polymarket API is unreachable."""
    portfolio, actuals = _open_one_position(tmp_path)

    calls = []
    def fake(slug):
        calls.append(slug)
        return {}
    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        fake,
    )

    settled_csv = tmp_path / "settled.csv"
    payload = settle_paper_portfolio(
        portfolio_path=portfolio,
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
        cross_check_polymarket=False,
    )

    assert calls == []
    assert payload["summary"]["polymarket_cross_check"] is None
