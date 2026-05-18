from __future__ import annotations

import csv
import json

from detect_temperature.paper import (
    OPEN_STATUSES,
    SETTLED_STATUSES,
    _position_from_signal,
    _position_from_strategy_row,
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


def test_position_from_signal_carries_identity_columns() -> None:
    """Regression for the data-quality bug: paper_portfolio.csv had 90 of
    90 rows missing target_date / station_id / target_extreme / city
    because _position_from_signal did not copy these fields from the
    signal row. Without them _stuck_paper_targets cannot reconstruct a
    MarketTarget for a stuck slug."""
    signal_row = {
        "event_slug": "highest-temperature-in-shanghai-on-may-12-2026",
        "event_title": "Shanghai", "market_slug": "shanghai-26c",
        "question": "?", "group_item_title": "26C",
        "paper_side": "BUY_NO", "paper_price": "0.5",
        "paper_fair_probability": "0.4", "paper_net_edge": "0.05",
        "yes_fee_per_share": "0.01", "no_fee_per_share": "0.01",
        "interval_lower": "25.5", "interval_upper": "26.5",
        "interval_unit": "celsius", "end_date": "2026-05-12T12:00:00Z",
        # The columns under test:
        "target_date": "2026-05-12",
        "station_id": "ZSPD",
        "target_extreme": "max",
        "city": "Shanghai",
        "target_unit": "celsius",
        "source_domain": "wunderground.com",
    }
    pos = _position_from_signal(signal_row, opened_at="2026-05-11T22:00:00Z", stake_usdc=0.25)
    assert pos["target_date"] == "2026-05-12"
    assert pos["station_id"] == "ZSPD"
    assert pos["target_extreme"] == "max"
    assert pos["city"] == "Shanghai"
    assert pos["target_unit"] == "celsius"
    assert pos["source_domain"] == "wunderground.com"


def test_position_from_strategy_row_carries_identity_columns() -> None:
    """Same data-quality regression but on the strategy_lab path,
    which is the writer actually used by daily_open_trades."""
    strategy_row = {
        "candidate_id": "x", "event_slug": "highest-temperature-in-tokyo-on-may-12-2026",
        "event_title": "Tokyo", "market_slug": "tokyo-25c",
        "side": "BUY_YES", "stake_usdc": "0.25", "price": "0.4",
        "execution_price": "0.41", "base_fair_probability": "0.55",
        "execution_fillable": "1", "execution_fill_ratio": "1.0",
        "execution_book_levels_used": "1",
        "execution_book_available_usdc": "10", "execution_token_id": "tok",
        "interval_lower": "24.5", "interval_upper": "25.5",
        "interval_unit": "celsius", "end_date": "2026-05-12T12:00:00Z",
        # The columns under test:
        "target_date": "2026-05-12",
        "station_id": "RJTT",
        "target_extreme": "max",
        "city": "Tokyo",
        "target_unit": "celsius",
        "source_domain": "wunderground.com",
    }
    pos = _position_from_strategy_row(
        strategy_row,
        opened_at="2026-05-11T22:00:00Z",
        execution_mode="taker",
        weather_fee_rate=0.05,
        maker_fee_rate=0.0,
    )
    assert pos is not None
    assert pos["target_date"] == "2026-05-12"
    assert pos["station_id"] == "RJTT"
    assert pos["target_extreme"] == "max"
    assert pos["city"] == "Tokyo"
    assert pos["target_unit"] == "celsius"
    assert pos["source_domain"] == "wunderground.com"


def test_position_from_signal_emits_blank_identity_when_signal_predates_fix() -> None:
    """Older signals.csv files (pre-fix) lack the identity columns. The
    writer must still produce a position with empty strings for them
    instead of crashing or emitting None values that blow up CSV writers."""
    legacy_row = {
        "event_slug": "highest-temperature-in-test-on-may-5-2026",
        "event_title": "Test", "market_slug": "test-17c",
        "question": "?", "group_item_title": "17C",
        "paper_side": "BUY_YES", "paper_price": "0.2",
        "paper_fair_probability": "0.6", "paper_net_edge": "0.39",
        "yes_fee_per_share": "0.008", "no_fee_per_share": "0.008",
        "interval_lower": "16.5", "interval_upper": "17.5",
        "interval_unit": "celsius", "end_date": "2099-05-05T12:00:00Z",
    }
    pos = _position_from_signal(legacy_row, opened_at="2026-05-04T22:00:00Z", stake_usdc=0.25)
    for col in ("target_date", "station_id", "target_extreme", "city", "target_unit", "source_domain"):
        assert pos[col] == "", f"{col} must default to empty string, got {pos[col]!r}"


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
    # Polymarket is authoritative when resolved. PM says YES won, so a
    # BUY_NO position must be marked lost - even though our actuals.csv
    # alone would have said NO won (actual=22 outside bucket [16.5,17.5]).
    assert row["status"] == "lost"
    assert float(row["pnl_usdc"]) < 0
    assert row["settle_authority"] == "polymarket_resolved"
    assert row["polymarket_yes_won"] == "1"
    # Cross-check still records 'disagree' to highlight that our actuals
    # disagreed with the on-chain answer; this is the audit signal.
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


def test_settle_reconciles_when_polymarket_resolves_after_preliminary(tmp_path, monkeypatch) -> None:
    """The Phase-N+1 case that motivated this whole subsystem.

    Day 1: actuals come in pending or stale, settle writes a preliminary
           verdict (we say BUY_NO won).
    Day 2: Polymarket finalises on-chain with the OPPOSITE verdict (YES
           won). Settle must:
           - flip status from won -> lost
           - reverse PnL (was +profit, becomes -stake)
           - record settle_correction_usdc as the delta
           - mark settle_authority = polymarket_resolved
           - never mutate this row again on subsequent runs
    """
    portfolio, actuals = _open_one_position(tmp_path)

    # Day 1: no PM data yet
    settled_csv = tmp_path / "settled.csv"
    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        lambda slug: {},
    )
    settle_paper_portfolio(
        portfolio_path=portfolio,
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
    )
    day1 = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))[0]
    assert day1["status"] == "won"
    assert day1["settle_authority"] == "actuals_preliminary"
    day1_pnl = float(day1["pnl_usdc"])
    assert day1_pnl > 0
    # Crucially: no settle_correction_usdc recorded yet on the preliminary
    assert float(day1["settle_correction_usdc"]) == 0.0

    # Day 2: polymarket finalises with YES won (opposite of our preliminary)
    from detect_temperature.polymarket_resolution import MarketResolution
    pm_yes_won = MarketResolution(
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
        lambda slug: {"highest-temperature-in-test-on-may-5-2026-17c": pm_yes_won},
    )
    payload = settle_paper_portfolio(
        portfolio_path=settled_csv,    # feed yesterday's output back in
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
    )
    day2 = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))[0]

    # Verdict must have been reversed
    assert day2["status"] == "lost"
    assert day2["settle_authority"] == "polymarket_resolved"
    day2_pnl = float(day2["pnl_usdc"])
    assert day2_pnl < 0
    # The correction equals the swing between yesterday's preliminary
    # and today's authoritative verdict.
    correction = float(day2["settle_correction_usdc"])
    assert abs(correction - (day2_pnl - day1_pnl)) < 1e-6
    # And the summary surfaces it for the dashboard / health.json.
    assert payload["summary"]["total_settle_correction_usdc"] == round(correction, 4)
    assert payload["summary"]["settle_authority_counts"]["polymarket_resolved"] == 1

    # Day 3: PM is still resolved. The row must NOT be mutated further.
    payload3 = settle_paper_portfolio(
        portfolio_path=settled_csv,
        actuals_path=actuals,
        output_path=settled_csv,
        bankroll_usdc=100.0,
    )
    day3 = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))[0]
    assert day3["status"] == day2["status"]
    assert day3["pnl_usdc"] == day2["pnl_usdc"]
    # No further correction on a frozen row
    assert float(day3["settle_correction_usdc"]) == correction


def test_settle_promotes_preliminary_to_authoritative_without_changing_verdict(tmp_path, monkeypatch) -> None:
    """When PM eventually resolves and AGREES with our preliminary, the
    row gets promoted to polymarket_resolved with zero correction. This
    is the happy path."""
    portfolio, actuals = _open_one_position(tmp_path)

    # Day 1: preliminary
    settled_csv = tmp_path / "settled.csv"
    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        lambda slug: {},
    )
    settle_paper_portfolio(
        portfolio_path=portfolio, actuals_path=actuals,
        output_path=settled_csv, bankroll_usdc=100.0,
    )
    day1 = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))[0]
    assert day1["status"] == "won"
    assert day1["settle_authority"] == "actuals_preliminary"
    day1_pnl = float(day1["pnl_usdc"])

    # Day 2: PM agrees (NO won)
    from detect_temperature.polymarket_resolution import MarketResolution
    pm_no_won = MarketResolution(
        market_slug="highest-temperature-in-test-on-may-5-2026-17c",
        closed=True,
        yes_outcome_price=0.0, no_outcome_price=1.0,
        outcome_prices_raw='["0", "1"]', uma_status="resolved",
        resolution_source="",
    )
    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        lambda slug: {"highest-temperature-in-test-on-may-5-2026-17c": pm_no_won},
    )
    settle_paper_portfolio(
        portfolio_path=settled_csv, actuals_path=actuals,
        output_path=settled_csv, bankroll_usdc=100.0,
    )
    day2 = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))[0]

    assert day2["status"] == "won"
    assert day2["settle_authority"] == "polymarket_resolved"
    assert float(day2["pnl_usdc"]) == day1_pnl    # PnL unchanged
    assert float(day2["settle_correction_usdc"]) == 0.0
    assert day2["settle_agreement"] == "agree"


def test_settle_does_not_overwrite_settle_agreement_on_frozen_rows(tmp_path, monkeypatch) -> None:
    """Regression for P2 #6 in the 2026-05-18 audit.

    A row promoted to settle_authority='polymarket_resolved' carries a
    correctly-computed settle_agreement (from the moment it was promoted).
    On every subsequent daily_settle, _fetch_polymarket_resolutions
    rightly skips it (no need to re-query — the verdict is frozen). But
    the old code still called _annotate_polymarket(polymarket_resolution=
    None), which writes settle_agreement='no_data' and erases the audit
    value that was already on the row.

    On the live system this turned 74 of 78 PM-authoritative rows into
    'no_data' over a few daily_settle cycles. Audit utility went to zero.
    """
    portfolio, actuals = _open_one_position(tmp_path)
    settled_csv = tmp_path / "settled.csv"

    # Day 1: PM finalises with NO won (matches our actuals NO-side bet)
    from detect_temperature.polymarket_resolution import MarketResolution
    pm_no_won = MarketResolution(
        market_slug="highest-temperature-in-test-on-may-5-2026-17c",
        closed=True, yes_outcome_price=0.0, no_outcome_price=1.0,
        outcome_prices_raw='["0", "1"]', uma_status="resolved",
        resolution_source="",
    )
    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        lambda slug: {"highest-temperature-in-test-on-may-5-2026-17c": pm_no_won},
    )
    settle_paper_portfolio(
        portfolio_path=portfolio, actuals_path=actuals,
        output_path=settled_csv, bankroll_usdc=100.0,
    )
    day1 = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))[0]
    assert day1["settle_authority"] == "polymarket_resolved"
    assert day1["settle_agreement"] == "agree", "first settle records the correct audit"
    day1_polymarket_yes = day1["polymarket_yes_won"]

    # Day 2..N: every subsequent daily_settle. The fetcher skips this row
    # because it's already polymarket_resolved (frozen). _settle_position
    # is still called for it but with polymarket_resolution=None.
    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        lambda slug: {},  # nothing returned for any slug
    )
    for _ in range(3):
        settle_paper_portfolio(
            portfolio_path=settled_csv, actuals_path=actuals,
            output_path=settled_csv, bankroll_usdc=100.0,
        )

    final = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))[0]
    # Row stays frozen and the audit MUST be preserved.
    assert final["settle_authority"] == "polymarket_resolved"
    assert final["settle_agreement"] == "agree", (
        "frozen-row settle_agreement was overwritten by 'no_data' across "
        "subsequent runs; this is exactly the P2 #6 bug"
    )
    assert final["polymarket_yes_won"] == day1_polymarket_yes
    # PnL must also be untouched
    assert float(final["pnl_usdc"]) == float(day1["pnl_usdc"])


def test_settle_recovers_no_data_agreement_on_frozen_row(tmp_path, monkeypatch) -> None:
    """Companion recovery path for the P2 #6 fix.

    74 of 78 PM-authoritative rows on the live system have
    settle_agreement='no_data' because of the prior bug. The fix in
    _settle_position only stops the bleeding — but those rows already
    have no_data on disk. _fetch_polymarket_resolutions must re-query
    such rows so the next daily_settle can rebuild the audit value
    properly (PnL is untouched because the frozen guard still applies).

    This test reproduces that exact scenario: a row already on disk
    with settle_authority=polymarket_resolved AND settle_agreement=
    no_data must trigger a fresh PM query and end up with a real
    'agree' or 'disagree' value. A row already at 'agree' must NOT
    be re-queried — that would be wasted API calls.
    """
    portfolio_path = tmp_path / "portfolio.csv"
    fieldnames = [
        "event_slug", "market_slug", "side", "shares", "stake_usdc",
        "interval_lower", "interval_upper", "interval_unit",
        "status", "won", "payout_usdc", "pnl_usdc",
        "settle_authority", "settle_agreement",
        "polymarket_yes_won", "polymarket_uma_status", "polymarket_outcome_prices",
    ]
    rows_in = [
        # Row A: previously buggy — frozen with no_data, must be re-queried
        {
            "event_slug": "highest-temperature-in-test-on-may-5-2026",
            "market_slug": "highest-temperature-in-test-on-may-5-2026-17c",
            "side": "BUY_NO", "shares": "1.0", "stake_usdc": "0.25",
            "interval_lower": "16.5", "interval_upper": "17.5",
            "interval_unit": "celsius",
            "status": "won", "won": "1", "payout_usdc": "1.0", "pnl_usdc": "0.75",
            "settle_authority": "polymarket_resolved",
            "settle_agreement": "no_data",  # <- the leftover bug state
            "polymarket_yes_won": "", "polymarket_uma_status": "",
            "polymarket_outcome_prices": "",
        },
        # Row B: clean frozen row with already-correct agreement
        {
            "event_slug": "highest-temperature-in-test-on-may-6-2026",
            "market_slug": "highest-temperature-in-test-on-may-6-2026-18c",
            "side": "BUY_NO", "shares": "1.0", "stake_usdc": "0.25",
            "interval_lower": "17.5", "interval_upper": "18.5",
            "interval_unit": "celsius",
            "status": "won", "won": "1", "payout_usdc": "1.0", "pnl_usdc": "0.75",
            "settle_authority": "polymarket_resolved",
            "settle_agreement": "agree",  # <- already audited correctly
            "polymarket_yes_won": "0",
            "polymarket_uma_status": "resolved",
            "polymarket_outcome_prices": '["0", "1"]',
        },
    ]
    with portfolio_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_in)

    actuals_path = tmp_path / "actuals.csv"
    actuals_path.write_text("slug,status\n", encoding="utf-8")

    queried_event_slugs: list[str] = []
    from detect_temperature.polymarket_resolution import MarketResolution
    pm_no_won = MarketResolution(
        market_slug="highest-temperature-in-test-on-may-5-2026-17c",
        closed=True, yes_outcome_price=0.0, no_outcome_price=1.0,
        outcome_prices_raw='["0", "1"]', uma_status="resolved",
        resolution_source="",
    )

    def fake_fetch(slug):
        queried_event_slugs.append(slug)
        if slug == "highest-temperature-in-test-on-may-5-2026":
            return {"highest-temperature-in-test-on-may-5-2026-17c": pm_no_won}
        return {}

    monkeypatch.setattr(
        "detect_temperature.polymarket_resolution.fetch_event_resolution",
        fake_fetch,
    )

    settled_csv = tmp_path / "settled.csv"
    settle_paper_portfolio(
        portfolio_path=portfolio_path, actuals_path=actuals_path,
        output_path=settled_csv, bankroll_usdc=100.0,
    )

    # Only the no_data row should have triggered a PM query.
    assert queried_event_slugs == ["highest-temperature-in-test-on-may-5-2026"]

    settled_rows = list(csv.DictReader(settled_csv.open(newline="", encoding="utf-8")))
    by_slug = {r["event_slug"]: r for r in settled_rows}

    # Row A: audit recovered, PnL untouched.
    a = by_slug["highest-temperature-in-test-on-may-5-2026"]
    assert a["settle_agreement"] == "agree", "no_data row must self-heal once we re-query"
    assert a["polymarket_yes_won"] == "0"
    assert a["status"] == "won"
    assert float(a["pnl_usdc"]) == 0.75

    # Row B: untouched, no wasted API.
    b = by_slug["highest-temperature-in-test-on-may-6-2026"]
    assert b["settle_agreement"] == "agree"
    assert b["polymarket_yes_won"] == "0"
