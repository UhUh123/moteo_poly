from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signals import fee_per_share as market_fee_per_share
from .units import celsius_to_fahrenheit


OPEN_STATUSES = {"open", "pending_actual", "at_risk"}
SETTLED_STATUSES = {"won", "lost"}


def open_paper_portfolio(
    signals_path: str | Path,
    output_path: str | Path,
    state_path: str | Path | None = None,
    dashboard_path: str | Path | None = None,
    bankroll_usdc: float = 1000.0,
    min_edge: float = 0.03,
    max_positions: int = 100,
    max_stake_usdc: float = 5.0,
    max_total_exposure_pct: float = 0.5,
    max_event_exposure_pct: float = 0.05,
    min_price: float = 0.005,
    max_price: float = 0.97,
    allow_ended: bool = False,
) -> dict[str, Any]:
    signals = _read_csv(signals_path)
    opened_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_exposure_cap = bankroll_usdc * max_total_exposure_pct
    event_exposure_cap = bankroll_usdc * max_event_exposure_pct
    total_exposure = 0.0
    event_exposure: dict[str, float] = {}
    positions = []

    candidates = [
        row for row in signals
        if row.get("paper_side") in {"BUY_YES", "BUY_NO"}
        and _as_float(row.get("paper_net_edge"), -999.0) >= min_edge
        and (allow_ended or row.get("market_has_ended") != "1")
        and min_price <= _as_float(row.get("paper_price"), -1.0) <= max_price
    ]
    candidates.sort(key=lambda row: _as_float(row.get("paper_net_edge"), -999.0), reverse=True)

    for row in candidates:
        if len(positions) >= max_positions:
            break
        event_slug = row.get("event_slug", "")
        stake = _candidate_stake(row, bankroll_usdc=bankroll_usdc, max_stake_usdc=max_stake_usdc)
        if stake <= 0:
            continue
        if total_exposure + stake > total_exposure_cap:
            continue
        if event_exposure.get(event_slug, 0.0) + stake > event_exposure_cap:
            continue

        position = _position_from_signal(row, opened_at=opened_at, stake_usdc=stake)
        positions.append(position)
        total_exposure += stake
        event_exposure[event_slug] = event_exposure.get(event_slug, 0.0) + stake

    summary = summarize_portfolio(positions, bankroll_usdc=bankroll_usdc, generated_at=opened_at)
    summary.update(
        {
            "source_signals_path": str(signals_path),
            "selection": {
                "min_edge": min_edge,
                "max_positions": max_positions,
                "max_stake_usdc": max_stake_usdc,
                "max_total_exposure_pct": max_total_exposure_pct,
                "max_event_exposure_pct": max_event_exposure_pct,
                "min_price": min_price,
                "max_price": max_price,
                "allow_ended": allow_ended,
            },
        }
    )
    _write_csv(positions, output_path)
    payload = {"summary": summary, "positions": positions}
    if state_path:
        _write_json(payload, state_path)
    if dashboard_path:
        render_paper_dashboard(payload, dashboard_path)
    return payload


def open_strategy_paper_portfolio(
    strategy_portfolio_path: str | Path,
    output_path: str | Path,
    state_path: str | Path | None = None,
    dashboard_path: str | Path | None = None,
    bankroll_usdc: float = 1000.0,
    max_positions: int = 100,
    execution_mode: str = "taker",
    weather_fee_rate: float = 0.05,
    maker_fee_rate: float = 0.0,
    preserve_open: bool = True,
) -> dict[str, Any]:
    rows = _read_csv(strategy_portfolio_path)
    opened_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Load existing portfolio so previous runs are preserved, not overwritten.
    # Open rows (still awaiting actuals), settled rows (won/lost with realised
    # PnL), and at_risk rows (flagged by near_close but not yet resolved) all
    # survive so the dashboard keeps the full history and settle can still
    # land them tomorrow morning.
    carried_positions: list[dict[str, Any]] = []
    carried_keys: set[tuple[str, str]] = set()
    if preserve_open:
        try:
            carried_positions = _read_csv(output_path)
        except FileNotFoundError:
            carried_positions = []
        for existing in carried_positions:
            status = str(existing.get("status") or "").lower()
            if status not in OPEN_STATUSES and status not in SETTLED_STATUSES:
                continue
            carried_keys.add(
                (
                    str(existing.get("event_slug") or ""),
                    str(existing.get("side") or ""),
                )
            )

    positions: list[dict[str, Any]] = [dict(p) for p in carried_positions]
    new_opened = 0
    for row in rows:
        if new_opened >= max_positions:
            break
        if row.get("selected", "1") not in {"", "1", 1, True}:
            continue
        position = _position_from_strategy_row(
            row,
            opened_at=opened_at,
            execution_mode=execution_mode,
            weather_fee_rate=weather_fee_rate,
            maker_fee_rate=maker_fee_rate,
        )
        if not position:
            continue
        dedupe_key = (
            str(position.get("event_slug") or ""),
            str(position.get("side") or ""),
        )
        if dedupe_key in carried_keys:
            # Already have an open or settled position for this (event, side).
            # Don't double-enter.
            continue
        carried_keys.add(dedupe_key)
        positions.append(position)
        new_opened += 1

    summary = summarize_portfolio(positions, bankroll_usdc=bankroll_usdc, generated_at=opened_at)
    summary.update(
        {
            "source_strategy_portfolio_path": str(strategy_portfolio_path),
            "paper_source": "strategy_lab",
            "carried_positions": len(carried_positions),
            "new_positions_added": new_opened,
            "selection": {
                "max_positions": max_positions,
                "execution_mode": execution_mode,
                "weather_fee_rate": weather_fee_rate,
                "maker_fee_rate": maker_fee_rate,
                "preserve_open": preserve_open,
            },
        }
    )
    _write_csv(positions, output_path)
    payload = {"summary": summary, "positions": positions}
    if state_path:
        _write_json(payload, state_path)
    if dashboard_path:
        render_paper_dashboard(payload, dashboard_path)
    return payload


def settle_paper_portfolio(
    portfolio_path: str | Path,
    actuals_path: str | Path,
    output_path: str | Path,
    state_path: str | Path | None = None,
    dashboard_path: str | Path | None = None,
    bankroll_usdc: float = 1000.0,
    cross_check_polymarket: bool = True,
) -> dict[str, Any]:
    positions = _read_csv(portfolio_path)
    actuals = {row.get("slug", ""): row for row in _read_csv(actuals_path)}
    settled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Best-effort independent cross-check against the on-chain Polymarket
    # resolution. Our actuals.csv is the source of truth for paper PnL —
    # this only annotates each position with what Polymarket itself
    # decided so we can audit divergence later.
    polymarket_resolutions = (
        _fetch_polymarket_resolutions(positions, actuals)
        if cross_check_polymarket
        else {}
    )

    updated_positions = []
    cross_check_stats = {"agree": 0, "disagree": 0, "pending": 0, "no_data": 0}
    authority_counts = {"polymarket_resolved": 0, "actuals_preliminary": 0}
    total_correction = 0.0
    for position in positions:
        market_slug = position.get("market_slug", "")
        market_resolution = polymarket_resolutions.get(market_slug)
        updated = _settle_position(
            position,
            actuals.get(position.get("event_slug", "")),
            settled_at,
            polymarket_resolution=market_resolution,
        )
        agreement = updated.get("settle_agreement", "")
        if agreement in cross_check_stats:
            cross_check_stats[agreement] += 1
        if updated.get("status") in SETTLED_STATUSES:
            auth = updated.get("settle_authority", "")
            if auth in authority_counts:
                authority_counts[auth] += 1
        correction = _as_float(updated.get("settle_correction_usdc"))
        if correction:
            total_correction += correction
        updated_positions.append(updated)

    summary = summarize_portfolio(updated_positions, bankroll_usdc=bankroll_usdc, generated_at=settled_at)
    summary.update(
        {
            "source_portfolio_path": str(portfolio_path),
            "source_actuals_path": str(actuals_path),
            "polymarket_cross_check": cross_check_stats if cross_check_polymarket else None,
            "settle_authority_counts": authority_counts if cross_check_polymarket else None,
            "total_settle_correction_usdc": round(total_correction, 4),
        }
    )
    _write_csv(updated_positions, output_path)
    payload = {"summary": summary, "positions": updated_positions}
    if state_path:
        _write_json(payload, state_path)
    if dashboard_path:
        render_paper_dashboard(payload, dashboard_path)
    return payload


def _fetch_polymarket_resolutions(
    positions: list[dict[str, Any]],
    actuals: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """For every event_slug that has at least one position eligible to
    settle (status open/at_risk/pending_actual AND its actual is ready),
    hit the Polymarket gamma API once and merge the per-market
    resolutions into a single market_slug -> MarketResolution map.

    Best-effort: any network or parse failure is logged and skipped.
    The settle still runs without cross-check data.
    """
    from .polymarket_resolution import fetch_event_resolution

    eligible_event_slugs: set[str] = set()
    for pos in positions:
        event_slug = pos.get("event_slug") or ""
        if not event_slug:
            continue
        if pos.get("status") in SETTLED_STATUSES:
            authority = pos.get("settle_authority", "")
            if authority == "polymarket_resolved":
                # Frozen final answer; we won't change PnL. But if the
                # audit columns were silently nuked by a prior buggy
                # daily_settle (P2 #6: _annotate_polymarket called with
                # polymarket_resolution=None replaces a real verdict
                # with "no_data"), we re-query so this run can rebuild
                # the audit. The frozen guard in _settle_position keeps
                # PnL untouched.
                agreement = (pos.get("settle_agreement") or "").strip()
                if agreement in {"", "no_data"}:
                    eligible_event_slugs.add(event_slug)
                continue
            # Either we never cross-checked, or the verdict is still
            # only 'actuals_preliminary' and may need reconciling.
            eligible_event_slugs.add(event_slug)
            continue
        # Newly-settling row: must also have a usable actual on disk
        actual = actuals.get(event_slug)
        if actual is None or actual.get("status") != "ok":
            # PM might still be authoritative even with no actuals yet
            eligible_event_slugs.add(event_slug)
            continue
        eligible_event_slugs.add(event_slug)

    merged: dict[str, Any] = {}
    for event_slug in sorted(eligible_event_slugs):
        try:
            event_markets = fetch_event_resolution(event_slug)
        except Exception:
            # Network error / 5xx / DNS hiccup. Best-effort — leave the
            # affected positions without cross-check annotation rather
            # than blow up the whole settle pass.
            continue
        merged.update(event_markets)
    return merged


def _settle_position(
    position: dict[str, Any],
    actual: dict[str, Any] | None,
    settled_at: str,
    polymarket_resolution: Any | None = None,
) -> dict[str, Any]:
    """Decide won/lost for a single open position, with on-chain
    Polymarket resolution treated as the authoritative truth.

    Source priority:
      1. polymarket_resolution.is_resolved   -> authoritative final.
      2. actual ok in actuals.csv            -> 'actuals_preliminary'.
      3. neither                             -> stay open / pending.

    Reconciliation rule. A previously-settled row with
    settle_authority="actuals_preliminary" gets re-evaluated when
    Polymarket eventually resolves. If the on-chain answer matches our
    preliminary, we just promote the row to "polymarket_resolved".
    If it disagrees, we REVERSE the verdict, recompute payout / pnl,
    and record the difference in settle_correction_usdc so PnL drift
    is auditable. Rows that were already "polymarket_resolved" are
    frozen and never edited.
    """
    updated = dict(position)
    side = updated.get("side")
    interval_unit = updated.get("interval_unit", "celsius")
    interval_lower = _as_optional_float(updated.get("interval_lower"))
    interval_upper = _as_optional_float(updated.get("interval_upper"))
    shares = _as_float(updated.get("shares"))
    stake = _as_float(updated.get("stake_usdc"))

    # ---- pick authoritative source ----------------------------------------
    pm_authoritative = polymarket_resolution is not None and polymarket_resolution.is_resolved
    actual_value, actual_status_label = _interpret_actual(actual, interval_unit)

    # Compute YES-side verdicts from each source separately so we can
    # compare them and decide reconciliation.
    pm_yes_won: bool | None = polymarket_resolution.yes_won if pm_authoritative else None
    actuals_yes_won: bool | None = None
    if actual_value is not None:
        actuals_yes_won = _contains_interval(actual_value, interval_lower, interval_upper)

    # ---- handle already-settled rows --------------------------------------
    prior_status = updated.get("status")
    if prior_status in SETTLED_STATUSES:
        prior_authority = updated.get("settle_authority", "")
        # For agreement audit we always compare what *actuals.csv* would say
        # against on-chain Polymarket, regardless of which source set the
        # final verdict. If we don't have a fresh actuals reading anymore,
        # fall back to the row's prior implied yes_won so old data still
        # carries an audit value.
        our_yes_won_for_audit = (
            actuals_yes_won
            if actuals_yes_won is not None
            else _settled_row_yes_won(updated)
        )
        if prior_authority == "polymarket_resolved":
            # Frozen. Only refresh audit columns when we ACTUALLY have a
            # fresh PM response in hand. Without this guard each
            # daily_settle re-run calls _annotate_polymarket with
            # polymarket_resolution=None (the fetcher skipped this row
            # because it's already authoritative), which would overwrite
            # the correct settle_agreement that was recorded the day
            # this row was promoted, replacing it with the synthesised
            # "no_data" value. See P2 #6 in the 2026-05-18 audit.
            if polymarket_resolution is not None:
                _annotate_polymarket(
                    updated,
                    polymarket_resolution,
                    our_yes_won=our_yes_won_for_audit,
                )
            return updated

        if pm_authoritative:
            # We have a final on-chain answer. Promote to authoritative.
            new_won_yes_perspective = pm_yes_won
            new_won = (
                new_won_yes_perspective if side == "BUY_YES" else not new_won_yes_perspective
            )
            old_won = _settled_row_yes_won(updated)
            old_pnl = _as_float(updated.get("pnl_usdc"))
            verdict_changed = old_won != new_won_yes_perspective
            new_payout = (shares if new_won else 0.0)
            new_pnl = new_payout - stake
            updated["settle_authority"] = "polymarket_resolved"
            updated["settled_at"] = settled_at
            updated["status"] = "won" if new_won else "lost"
            updated["won"] = int(new_won)
            updated["payout_usdc"] = round(new_payout, 4)
            updated["pnl_usdc"] = round(new_pnl, 4)
            updated["roi_pct"] = round((new_pnl / stake) * 100.0, 2) if stake else None
            updated["settle_correction_usdc"] = (
                round(new_pnl - old_pnl, 4) if verdict_changed else 0.0
            )
            _annotate_polymarket(updated, polymarket_resolution, our_yes_won=our_yes_won_for_audit)
            return updated

        # No authoritative PM yet, keep the prior verdict as-is and just
        # backfill cross-check columns so health/dashboard stay current.
        _annotate_polymarket(
            updated,
            polymarket_resolution,
            our_yes_won=our_yes_won_for_audit,
        )
        if "settle_authority" not in updated or not updated["settle_authority"]:
            updated["settle_authority"] = "actuals_preliminary"
        return updated

    # ---- open / unsettled rows --------------------------------------------
    # Audit is always 'what would actuals alone have said' vs PM, even when
    # PM is the authority for the actual verdict.
    audit_yes_won = actuals_yes_won

    if pm_authoritative:
        verdict_yes_won = pm_yes_won
        won = (verdict_yes_won if side == "BUY_YES" else not verdict_yes_won)
        payout = shares if won else 0.0
        pnl = payout - stake
        updated["status"] = "won" if won else "lost"
        updated["settled_at"] = settled_at
        updated["actual_value"] = (
            round(actual_value, 4) if actual_value is not None else updated.get("actual_value", "")
        )
        updated["actual_status"] = actual_status_label
        updated["won"] = int(won)
        updated["payout_usdc"] = round(payout, 4)
        updated["pnl_usdc"] = round(pnl, 4)
        updated["roi_pct"] = round((pnl / stake) * 100.0, 2) if stake else None
        updated["settle_authority"] = "polymarket_resolved"
        updated["settle_correction_usdc"] = 0.0
        _annotate_polymarket(updated, polymarket_resolution, our_yes_won=audit_yes_won)
        return updated

    # No PM resolution. Fall back to actuals.
    if actual is None:
        updated["status"] = updated.get("status") or "open"
        updated["actual_status"] = "missing"
        return updated
    updated["actual_status"] = actual_status_label
    if actual_status_label != "ok" or actuals_yes_won is None:
        updated["status"] = updated.get("status") or "open"
        return updated

    won = actuals_yes_won if side == "BUY_YES" else not actuals_yes_won
    payout = shares if won else 0.0
    pnl = payout - stake
    updated["status"] = "won" if won else "lost"
    updated["settled_at"] = settled_at
    updated["actual_value"] = round(actual_value, 4)
    updated["won"] = int(won)
    updated["payout_usdc"] = round(payout, 4)
    updated["pnl_usdc"] = round(pnl, 4)
    updated["roi_pct"] = round((pnl / stake) * 100.0, 2) if stake else None
    updated["settle_authority"] = "actuals_preliminary"
    updated["settle_correction_usdc"] = 0.0
    _annotate_polymarket(updated, polymarket_resolution, our_yes_won=audit_yes_won)
    return updated


def _interpret_actual(
    actual: dict[str, Any] | None, interval_unit: str
) -> tuple[float | None, str]:
    """Translate an actuals.csv row into (value-in-bucket-unit, status_label).

    status_label is what gets written to the position's `actual_status`
    column: 'ok', 'missing', or whatever upstream reported (pending/error).
    """
    if actual is None:
        return None, "missing"
    label = actual.get("status", "") or ""
    if label != "ok":
        return None, label
    obs_c = _as_float(actual.get("observed_temp_c"))
    if obs_c is None:
        return None, label
    value = celsius_to_fahrenheit(obs_c) if interval_unit == "fahrenheit" else obs_c
    return value, "ok"


def _settled_row_yes_won(row: dict[str, Any]) -> bool:
    """Reverse-engineer YES-side verdict from a previously-written row.

    BUY_YES + won=1 -> yes_won=True; BUY_NO + won=1 -> yes_won=False; etc.
    Falls back to status if `won` is missing.
    """
    raw = row.get("won")
    try:
        our_won_bool = bool(int(raw))
    except (TypeError, ValueError):
        our_won_bool = row.get("status") == "won"
    side = row.get("side")
    if side == "BUY_NO":
        return not our_won_bool
    return our_won_bool


def _annotate_polymarket(
    row: dict[str, Any],
    polymarket_resolution: Any | None,
    our_yes_won: bool,
) -> None:
    """Add four cross-check columns to a settled row in-place. PnL is
    untouched — this is audit only."""
    from .polymarket_resolution import settle_agreement as _settle_agreement

    if polymarket_resolution is None:
        pm_yes_won_int: int | str = ""
        pm_uma_status = ""
        pm_outcome_prices = ""
    else:
        pm_yes = polymarket_resolution.yes_won
        pm_yes_won_int = "" if pm_yes is None else int(pm_yes)
        pm_uma_status = polymarket_resolution.uma_status
        pm_outcome_prices = polymarket_resolution.outcome_prices_raw
    row["polymarket_yes_won"] = pm_yes_won_int
    row["polymarket_uma_status"] = pm_uma_status
    row["polymarket_outcome_prices"] = pm_outcome_prices
    row["settle_agreement"] = _settle_agreement(
        our_yes_won=our_yes_won,
        polymarket_resolution=polymarket_resolution,
    )


def summarize_portfolio(
    positions: list[dict[str, Any]],
    bankroll_usdc: float,
    generated_at: str,
) -> dict[str, Any]:
    total_staked = sum(_as_float(row.get("stake_usdc")) for row in positions)
    settled = [row for row in positions if row.get("status") in SETTLED_STATUSES]
    open_rows = [row for row in positions if row.get("status") in OPEN_STATUSES]
    won = [row for row in positions if row.get("status") == "won"]
    lost = [row for row in positions if row.get("status") == "lost"]
    realized_payout = sum(_as_float(row.get("payout_usdc")) for row in settled)
    settled_stake = sum(_as_float(row.get("stake_usdc")) for row in settled)
    open_expected_payout = sum(_as_float(row.get("expected_payout_usdc")) for row in open_rows)
    cash_balance = bankroll_usdc - total_staked + realized_payout
    expected_equity = cash_balance + open_expected_payout
    realized_pnl = realized_payout - settled_stake
    expected_pnl = expected_equity - bankroll_usdc
    win_rate = (len(won) / len(settled) * 100.0) if settled else None
    strategy_lab_positions = sum(1 for row in positions if row.get("paper_source") == "strategy_lab")
    taker_positions = sum(1 for row in positions if row.get("entry_mode", "taker") == "taker")
    maker_positions = sum(1 for row in positions if row.get("entry_mode") == "maker")
    maker_preferred_positions = sum(1 for row in positions if str(row.get("maker_preferred", "")) == "1")
    depth_checked_positions = sum(1 for row in positions if str(row.get("execution_fill_ratio", "")).strip())
    fillable_positions = sum(1 for row in positions if str(row.get("execution_fillable", "1")) in {"", "1", "True", "true"})
    return {
        "generated_at": generated_at,
        "bankroll_usdc": round(bankroll_usdc, 2),
        "positions": len(positions),
        "open_positions": len(open_rows),
        "settled_positions": len(settled),
        "won_positions": len(won),
        "lost_positions": len(lost),
        "win_rate_pct": _round_or_none(win_rate),
        "total_staked_usdc": round(total_staked, 4),
        "settled_stake_usdc": round(settled_stake, 4),
        "cash_balance_usdc": round(cash_balance, 4),
        "realized_payout_usdc": round(realized_payout, 4),
        "realized_pnl_usdc": round(realized_pnl, 4),
        "open_expected_payout_usdc": round(open_expected_payout, 4),
        "expected_equity_usdc": round(expected_equity, 4),
        "expected_total_pnl_usdc": round(expected_pnl, 4),
        "strategy_lab_positions": strategy_lab_positions,
        "taker_positions": taker_positions,
        "maker_positions": maker_positions,
        "maker_preferred_positions": maker_preferred_positions,
        "depth_checked_positions": depth_checked_positions,
        "fillable_positions": fillable_positions,
    }


def render_paper_dashboard(payload: dict[str, Any], path: str | Path) -> None:
    summary = payload.get("summary", {})
    actuals = payload.get("actuals", {})
    positions = payload.get("positions", [])
    data_json = json.dumps(payload, ensure_ascii=False)
    rows_html = "\n".join(_position_row_html(position) for position in positions)
    cards = [
        (
            "Bankroll",
            _money(summary.get("bankroll_usdc")),
            "",
            "",
            "Виртуальный стартовый баланс для paper-теста. Это не реальные деньги; число влияет только на размер ставок и лимиты риска.",
        ),
        (
            "Cash",
            _money(summary.get("cash_balance_usdc")),
            "",
            "",
            "Свободные виртуальные деньги: баланс минус открытые ставки плюс выплаты по уже закрытым позициям.",
        ),
        (
            "Open",
            str(summary.get("open_positions", 0)),
            "",
            "positions",
            "Позиции, по которым еще нет финальной температуры. Они пока не выиграли и не проиграли.",
        ),
        (
            "Settled",
            str(summary.get("settled_positions", 0)),
            "",
            "positions",
            "Позиции, которые уже проверены по фактической температуре и получили статус won или lost.",
        ),
        (
            "Realized PnL",
            _money(summary.get("realized_pnl_usdc")),
            _pnl_class(summary.get("realized_pnl_usdc")),
            "",
            "Фактическая виртуальная прибыль или убыток по уже закрытым позициям. Это главный показатель после появления actuals.",
        ),
        (
            "Expected PnL",
            _money(summary.get("expected_total_pnl_usdc")),
            _pnl_class(summary.get("expected_total_pnl_usdc")),
            "",
            "Ожидаемая прибыль по оценке модели. Это прогноз, а не факт; он может сильно ошибаться.",
        ),
        (
            "Robust",
            str(summary.get("strategy_lab_positions", 0)),
            "",
            "positions",
            "Сколько позиций пришло из Strategy Lab после stress-фильтра, execution penalty и лимитов концентрации.",
        ),
        (
            "Entry Mix",
            f"T {summary.get('taker_positions', 0)} / M {summary.get('maker_positions', 0)}",
            "",
            "",
            "Taker - paper-вход по цене с учетом estimated slippage. Maker - гипотетическая лимитка; её реальное исполнение нужно проверять отдельно.",
        ),
        (
            "Maker Pref",
            str(summary.get("maker_preferred_positions", 0)),
            "",
            "positions",
            "Позиции, где Strategy Lab считает maker-вход потенциально лучше taker-входа по fill-adjusted ожиданию.",
        ),
        (
            "Fillable",
            f"{summary.get('fillable_positions', 0)} / {summary.get('positions', 0)}",
            "",
            "positions",
            "Сколько paper-позиций прошли проверку глубины стакана. Если стакан не подключен, поле не доказывает реальный fill.",
        ),
        (
            "Win Rate",
            _percent(summary.get("win_rate_pct")),
            "",
            "",
            "Доля выигравших позиций среди уже закрытых. Пусто, пока нет ни одной закрытой позиции.",
        ),
        (
            "Actuals OK",
            str(actuals.get("ok", "-")),
            "",
            "markets",
            "Сколько рынков уже получили фактическую температуру из resolution source.",
        ),
        (
            "Actuals Pending",
            str(actuals.get("pending", "-")),
            "",
            "markets",
            "Сколько рынков еще ждут финализации или пока не имеют доступных данных.",
        ),
        (
            "Actuals Error",
            str(actuals.get("error", "-")),
            "negative" if actuals.get("error") else "",
            "markets",
            "Сколько рынков не удалось обновить из-за ошибки источника, например timeout. Обычно можно повторить позже.",
        ),
    ]
    cards_html = "\n".join(
        (
            '<section class="metric">'
            f'<span class="has-tip" data-tip="{html.escape(tooltip, quote=True)}">{html.escape(label)}</span>'
            f'<strong class="{klass}">{html.escape(value)}</strong>'
            f'<em>{html.escape(note)}</em>'
            '</section>'
        )
        for label, value, klass, note, tooltip in cards
    )
    table_headers = [
        ("Status", "open значит ждем фактическую температуру. won/lost появится после проверки результата."),
        ("Trade", "Сторона, outcome и рынок. Наведи на строку, чтобы увидеть полное объяснение сигнала."),
        ("Stake", "Сколько виртуальных USDC поставлено на эту paper-позицию."),
        ("Price", "Цена paper-входа. Для Strategy Lab taker-режима это цена уже с estimated slippage."),
        ("Fair / Edge", "Fair - наша вероятность выигрыша выбранной стороны. Edge - запас над ценой и комиссией."),
        ("Model", "Главный bucket модели и точечный прогноз температуры."),
        ("PnL", "Для открытых позиций показывает ожидаемый PnL. Для закрытых - фактический выигрыш или проигрыш."),
        ("Actual", "Фактическая resolved-температура, когда она будет собрана."),
    ]
    header_html = "".join(
        f'<th class="has-tip" data-tip="{html.escape(tip, quote=True)}">{html.escape(label)}</th>'
        for label, tip in table_headers
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paper Weather Desk</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #637083;
      --line: #d8dee8;
      --accent: #0f766e;
      --accent-2: #b45309;
      --good: #0f7a45;
      --bad: #b42318;
      --chip: #eef2f6;
      --warn-bg: #fff7ed;
      --warn-line: #fed7aa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    header {{
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{ margin: 0; font-size: 20px; font-weight: 700; }}
    .sub {{ color: var(--muted); font-size: 13px; }}
    main {{ padding: 18px 24px 28px; }}
    .header-actions {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      font-weight: 650;
    }}
    .primary:disabled {{
      opacity: 0.65;
      cursor: wait;
    }}
    #refreshStatus {{
      width: 360px;
      max-width: 40vw;
      color: var(--muted);
      font-size: 12px;
      text-align: right;
    }}
    .notice {{
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin-bottom: 14px;
      padding: 10px 12px;
      border: 1px solid var(--warn-line);
      background: var(--warn-bg);
      border-radius: 8px;
      color: #7c2d12;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      min-height: 72px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 21px; }}
    .metric em {{ color: var(--muted); font-style: normal; font-size: 12px; }}
    .positive {{ color: var(--good); }}
    .negative {{ color: var(--bad); }}
    .toolbar {{
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      margin: 14px 0;
    }}
    .tabs {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 7px 10px;
      color: var(--text);
      cursor: pointer;
    }}
    button.active {{ border-color: var(--accent); color: var(--accent); background: #ecfdf5; }}
    .has-tip {{
      cursor: help;
      text-decoration: underline dotted rgba(99, 112, 131, 0.75);
      text-underline-offset: 3px;
    }}
    #floatingTooltip {{
      position: fixed;
      z-index: 10000;
      display: none;
      max-width: min(340px, calc(100vw - 24px));
      padding: 9px 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #111827;
      color: #fff;
      box-shadow: 0 14px 30px rgba(15, 23, 42, 0.22);
      font-size: 12px;
      font-weight: 500;
      line-height: 1.35;
      pointer-events: none;
      white-space: normal;
    }}
    input {{
      min-width: 240px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 8px 10px;
      color: var(--text);
    }}
    .table-wrap {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
    }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    col.status {{ width: 86px; }}
    col.trade {{ width: 34%; }}
    col.stake {{ width: 82px; }}
    col.price {{ width: 76px; }}
    col.fair {{ width: 104px; }}
    col.model {{ width: 18%; }}
    col.pnl {{ width: 96px; }}
    col.actual {{ width: 86px; }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    td.num {{ white-space: nowrap; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 650; background: #fafbfc; position: sticky; top: 0; }}
    td.trade-cell {{ min-width: 0; }}
    .trade-main {{
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
      font-weight: 650;
    }}
    .trade-main span:last-child {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .trade-sub, .cell-sub {{
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.25;
    }}
    .trade-sub {{
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .chip {{ display: inline-flex; align-items: center; min-height: 22px; border-radius: 999px; padding: 2px 8px; background: var(--chip); font-size: 12px; }}
    .chip.at_risk {{ background: var(--warn-bg); color: #7c2d12; border: 1px solid var(--warn-line); }}
    .chip.won {{ background: #dcfce7; color: var(--good); }}
    .chip.lost {{ background: #fee2e2; color: var(--bad); }}
    .buy_yes {{ color: var(--accent); }}
    .buy_no {{ color: var(--accent-2); }}
    .won {{ color: var(--good); }}
    .lost {{ color: var(--bad); }}
    footer {{ color: var(--muted); padding: 14px 24px 24px; font-size: 12px; }}
    @media (max-width: 1000px) {{
      .metrics {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
      header, main, footer {{ padding-left: 14px; padding-right: 14px; }}
      .toolbar {{ align-items: stretch; flex-direction: column; }}
      .header-actions {{ justify-content: flex-start; }}
      #refreshStatus {{ width: 100%; max-width: none; text-align: left; }}
      input {{ min-width: 0; width: 100%; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Paper Weather Desk</h1>
      <div class="sub">Обновлено {html.escape(str(summary.get("generated_at", "")))} · paper-only, без реальных ордеров</div>
    </div>
    <div class="header-actions">
      <button id="dryRunButton">Проверить рынок</button>
      <button id="openTradesButton">Открыть сделки</button>
      <button id="refreshOpenButton">Мониторить позиции</button>
      <button class="primary" id="refreshButton">Обновить actuals & PnL</button>
      <div id="refreshStatus">Кнопки работают через локальный dashboard server.</div>
    </div>
  </header>
  <main>
    <div class="notice">
      <strong>Paper mode.</strong>
      <span>Реальные ордера не выставляются. Обновление собирает фактические температуры, проверяет выигрыш/проигрыш paper-позиций и пересчитывает виртуальный PnL.</span>
    </div>
    <div class="metrics">{cards_html}</div>
    <div class="toolbar">
      <div class="tabs">
        <button class="active" data-filter="all">Все</button>
        <button data-filter="open">Открытые</button>
        <button data-filter="settled">Закрытые</button>
        <button data-filter="won">Win</button>
        <button data-filter="lost">Loss</button>
      </div>
      <input id="search" placeholder="Поиск: город, рынок, сторона">
    </div>
    <div class="table-wrap">
      <table>
        <colgroup>
          <col class="status">
          <col class="trade">
          <col class="stake">
          <col class="price">
          <col class="fair">
          <col class="model">
          <col class="pnl">
          <col class="actual">
        </colgroup>
        <thead>
          <tr>{header_html}</tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </main>
  <footer>Виртуальный PnL считает, что сделки исполнились по записанной paper-цене, и не учитывает будущие движения стакана. Это только forward-test.</footer>
  <div id="floatingTooltip" role="tooltip"></div>
  <script id="paper-data" type="application/json">{html.escape(data_json)}</script>
  <script>
    const buttons = [...document.querySelectorAll('button[data-filter]')];
    const search = document.getElementById('search');
    const refreshButton = document.getElementById('refreshButton');
    const openTradesButton = document.getElementById('openTradesButton');
    const dryRunButton = document.getElementById('dryRunButton');
    const refreshOpenButton = document.getElementById('refreshOpenButton');
    const refreshStatus = document.getElementById('refreshStatus');
    const tooltip = document.getElementById('floatingTooltip');
    function applyFilter() {{
      const active = document.querySelector('button.active').dataset.filter;
      const term = search.value.trim().toLowerCase();
      document.querySelectorAll('tbody tr').forEach(row => {{
        const status = row.dataset.status;
        const text = row.textContent.toLowerCase();
        const statusOk = active === 'all'
          || status === active
          || (active === 'open' && (status === 'open' || status === 'pending_actual'))
          || (active === 'settled' && (status === 'won' || status === 'lost'));
        row.style.display = statusOk && (!term || text.includes(term)) ? '' : 'none';
      }});
    }}
    buttons.forEach(button => button.addEventListener('click', () => {{
      buttons.forEach(item => item.classList.remove('active'));
      button.classList.add('active');
      applyFilter();
    }}));
    search.addEventListener('input', applyFilter);
    function showTooltip(target) {{
      const tip = target.dataset.tip;
      if (!tip) return;
      tooltip.textContent = tip;
      tooltip.style.display = 'block';
      const rect = target.getBoundingClientRect();
      const tooltipRect = tooltip.getBoundingClientRect();
      let left = rect.left + rect.width / 2 - tooltipRect.width / 2;
      left = Math.max(12, Math.min(left, window.innerWidth - tooltipRect.width - 12));
      let top = rect.top - tooltipRect.height - 10;
      if (top < 12) {{
        top = rect.bottom + 10;
      }}
      tooltip.style.left = `${{left}}px`;
      tooltip.style.top = `${{top}}px`;
    }}
    function hideTooltip() {{
      tooltip.style.display = 'none';
    }}
    document.querySelectorAll('[data-tip]').forEach(item => {{
      item.addEventListener('mouseenter', () => showTooltip(item));
      item.addEventListener('focus', () => showTooltip(item));
      item.addEventListener('mouseleave', hideTooltip);
      item.addEventListener('blur', hideTooltip);
    }});
    window.addEventListener('scroll', hideTooltip, true);
    window.addEventListener('resize', hideTooltip);
    refreshButton.addEventListener('click', async () => {{
      if (window.location.protocol === 'file:') {{
        refreshStatus.textContent = 'Сначала открой через локальный сервер: python3 scripts/serve_paper_dashboard.py';
        return;
      }}
      refreshButton.disabled = true;
      refreshStatus.textContent = 'Собираю actuals и пересчитываю paper PnL...';
      try {{
        const response = await fetch('/api/refresh-paper', {{ method: 'POST' }});
        const data = await response.json();
        if (!response.ok) {{
          throw new Error(data.error || 'refresh failed');
        }}
        const s = data.summary || {{}};
        refreshStatus.textContent = `Готово: закрыто ${{s.settled_positions || 0}}, открыто ${{s.open_positions || 0}}, realized PnL ${{s.realized_pnl_usdc || 0}} USDC. Перезагружаю...`;
        setTimeout(() => window.location.reload(), 900);
      }} catch (error) {{
        refreshStatus.textContent = `Не удалось обновить: ${{error.message}}`;
      }} finally {{
        refreshButton.disabled = false;
      }}
    }});

    async function callPipeline(url, label, button) {{
      if (window.location.protocol === 'file:') {{
        refreshStatus.textContent = 'Сначала открой через локальный сервер: python3 scripts/serve_paper_dashboard.py';
        return;
      }}
      button.disabled = true;
      refreshStatus.textContent = `${{label}}... Это займёт 30–90 секунд (scan + predict + strategy lab).`;
      try {{
        const response = await fetch(url, {{ method: 'POST' }});
        const data = await response.json();
        if (!response.ok) {{
          if (data.kind === 'drawdown') {{
            refreshStatus.textContent = `Kill-switch: ${{data.error}}. Пересмотри стратегию вручную.`;
          }} else {{
            throw new Error(data.error || `${{label}} failed`);
          }}
          return;
        }}
        const lab = (data.strategy_lab || (data.market_pipeline && data.market_pipeline.strategy_lab)) || {{}};
        const paper = data.paper_summary || {{}};
        const parts = [];
        if (lab.trade_candidates !== undefined) parts.push(`candidates ${{lab.trade_candidates}}`);
        if (lab.robust_pass !== undefined) parts.push(`robust ${{lab.robust_pass}}`);
        if (lab.selected_positions !== undefined) parts.push(`selected ${{lab.selected_positions}}`);
        if (paper.positions !== undefined) parts.push(`opened ${{paper.positions}} @ $${{paper.total_staked_usdc || 0}}`);
        refreshStatus.textContent = `Готово: ${{parts.join(', ')}}. Перезагружаю...`;
        setTimeout(() => window.location.reload(), 1200);
      }} catch (error) {{
        refreshStatus.textContent = `Не удалось ${{label.toLowerCase()}}: ${{error.message}}`;
      }} finally {{
        button.disabled = false;
      }}
    }}

    openTradesButton.addEventListener('click', () => callPipeline('/api/open-trades', 'Открыть сделки', openTradesButton));
    dryRunButton.addEventListener('click', () => callPipeline('/api/dry-run', 'Проверка рынка', dryRunButton));

    refreshOpenButton.addEventListener('click', async () => {{
      if (window.location.protocol === 'file:') {{
        refreshStatus.textContent = 'Сначала открой через локальный сервер: python3 scripts/serve_paper_dashboard.py';
        return;
      }}
      refreshOpenButton.disabled = true;
      refreshStatus.textContent = 'Проверяю observed max/min и пересчитываю открытые позиции...';
      try {{
        const response = await fetch('/api/refresh-open', {{ method: 'POST' }});
        const data = await response.json();
        if (!response.ok) {{
          throw new Error(data.error || 'refresh-open failed');
        }}
        const stats = (data.summary && data.summary.refresh_stats) || {{}};
        refreshStatus.textContent =
          `Готово: обновлено ${{stats.refreshed || 0}}, resolved won ${{stats.resolved_won || 0}}, ` +
          `resolved lost ${{stats.resolved_lost || 0}}, at_risk ${{stats.at_risk || 0}}. Перезагружаю...`;
        setTimeout(() => window.location.reload(), 1100);
      }} catch (error) {{
        refreshStatus.textContent = `Не удалось обновить позиции: ${{error.message}}`;
      }} finally {{
        refreshOpenButton.disabled = false;
      }}
    }});
  </script>
</body>
</html>
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(document, encoding="utf-8")


def _position_from_signal(row: dict[str, Any], opened_at: str, stake_usdc: float) -> dict[str, Any]:
    side = row.get("paper_side", "")
    price = _as_float(row.get("paper_price"))
    fee_per_share = _as_float(row.get("yes_fee_per_share" if side == "BUY_YES" else "no_fee_per_share"))
    fair_probability = _as_float(row.get("paper_fair_probability"))
    denominator = price + fee_per_share
    shares = stake_usdc / denominator if denominator > 0 else 0.0
    expected_payout = shares * fair_probability
    payload = {
        "opened_at": opened_at,
        "status": "open",
        "event_slug": row.get("event_slug", ""),
        "event_title": row.get("event_title", ""),
        "market_slug": row.get("market_slug", ""),
        "question": row.get("question", ""),
        "group_item_title": row.get("group_item_title", ""),
        # Identity / classification columns carried from signals row so that
        # _stuck_paper_targets can later reconstruct a MarketTarget for this
        # slug without round-tripping through archived targets.csv. Empty
        # strings are fine if absent (older snapshots predating the fix).
        "target_date": row.get("target_date", ""),
        "station_id": row.get("station_id", ""),
        "target_extreme": row.get("target_extreme", ""),
        "city": row.get("city", ""),
        "target_unit": row.get("target_unit", ""),
        "source_domain": row.get("source_domain", ""),
        "side": side,
        "entry_mode": "taker",
        "paper_source": "signals",
        "stake_usdc": round(stake_usdc, 4),
        "price": round(price, 6),
        "fee_per_share": round(fee_per_share, 6),
        "shares": round(shares, 6),
        "fair_probability": round(fair_probability, 6),
        "net_edge": _round_or_none(_as_float(row.get("paper_net_edge"))),
        "strategy_version": row.get("strategy_version", ""),
        "execution_quality": row.get("execution_quality", ""),
        "maker_preferred": row.get("maker_preferred", ""),
        "maker_quote_price": row.get("maker_quote_price", ""),
        "maker_fill_score": row.get("maker_fill_score", ""),
        "maker_reason": row.get("maker_reason", ""),
        "robust_reason": row.get("robust_reason", ""),
        "decision_reason": row.get("decision_reason", row.get("reason", "")),
        "risk_flags": row.get("risk_flags", ""),
        "visible_top_bucket": row.get("visible_top_bucket", ""),
        "visible_top_bucket_probability": row.get("visible_top_bucket_probability", ""),
        "visible_bucket_rank": row.get("visible_bucket_rank", ""),
        "visible_bucket_count": row.get("visible_bucket_count", ""),
        "fair_yes_probability": row.get("fair_yes_probability", ""),
        "fair_no_probability": row.get("fair_no_probability", ""),
        "yes_net_edge": row.get("yes_net_edge", ""),
        "no_net_edge": row.get("no_net_edge", ""),
        "expected_payout_usdc": round(expected_payout, 4),
        "expected_pnl_usdc": round(expected_payout - stake_usdc, 4),
        "prediction_c": row.get("prediction_c", ""),
        "interval_lower": row.get("interval_lower", ""),
        "interval_upper": row.get("interval_upper", ""),
        "interval_unit": row.get("interval_unit", ""),
        "end_date": row.get("end_date", ""),
        "market_has_ended": row.get("market_has_ended", "0"),
        "actual_value": "",
        "actual_status": "",
        "won": "",
        "payout_usdc": "",
        "pnl_usdc": "",
        "roi_pct": "",
    }
    payload["trade_id"] = _trade_id(payload)
    return payload


def _position_from_strategy_row(
    row: dict[str, Any],
    opened_at: str,
    execution_mode: str,
    weather_fee_rate: float,
    maker_fee_rate: float,
) -> dict[str, Any] | None:
    side = row.get("side", "")
    stake_usdc = _as_float(row.get("stake_usdc"))
    if side not in {"BUY_YES", "BUY_NO"} or stake_usdc <= 0:
        return None

    entry_mode = _strategy_entry_mode(row, execution_mode)
    if entry_mode is None:
        return None
    price_key = "maker_quote_price" if entry_mode == "maker" else "execution_price"
    fallback_price_key = "price"
    price = _as_optional_float(row.get(price_key))
    if price is None:
        price = _as_optional_float(row.get(fallback_price_key))
    if price is None or price <= 0:
        return None

    fee_rate = maker_fee_rate if entry_mode == "maker" else weather_fee_rate
    fee = market_fee_per_share(price, fee_rate)
    fair_probability = _as_float(row.get("base_fair_probability"))
    expected_roi = _strategy_expected_roi(row, entry_mode)
    denominator = price + fee
    shares = stake_usdc / denominator if denominator > 0 else 0.0
    expected_payout = stake_usdc * (1.0 + expected_roi) if expected_roi is not None else shares * fair_probability
    net_edge_key = "maker_edge_if_filled" if entry_mode == "maker" else "execution_base_edge"
    payload = {
        "opened_at": opened_at,
        "status": "open",
        "event_slug": row.get("event_slug", ""),
        "event_title": row.get("event_title", ""),
        "market_slug": row.get("market_slug", ""),
        "question": row.get("event_title", ""),
        "group_item_title": row.get("group_item_title", ""),
        # Identity / classification columns; same rationale as
        # _position_from_signal above. The strategy_lab row inherits them
        # from market_signals.csv via signals.build_market_signal.
        "target_date": row.get("target_date", ""),
        "station_id": row.get("station_id", ""),
        "target_extreme": row.get("target_extreme", ""),
        "city": row.get("city", ""),
        "target_unit": row.get("target_unit", ""),
        "source_domain": row.get("source_domain", ""),
        "side": side,
        "entry_mode": entry_mode,
        "paper_source": "strategy_lab",
        "stake_usdc": round(stake_usdc, 4),
        "price": round(price, 6),
        "fee_per_share": round(fee, 6),
        "shares": round(shares, 6),
        "fair_probability": round(fair_probability, 6),
        "net_edge": _round_or_none(_as_optional_float(row.get(net_edge_key))),
        "strategy_version": "strategy_lab_v2",
        "strategy_candidate_id": row.get("candidate_id", ""),
        "execution_quality": row.get("execution_quality", ""),
        "execution_slippage": row.get("execution_slippage", ""),
        "maker_preferred": row.get("maker_preferred", ""),
        "maker_quote_price": row.get("maker_quote_price", ""),
        "maker_fill_score": row.get("maker_fill_score", ""),
        "maker_reason": row.get("maker_reason", ""),
        "maker_edge_if_filled": row.get("maker_edge_if_filled", ""),
        "maker_worst_edge": row.get("maker_worst_edge", ""),
        "execution_fillable": row.get("execution_fillable", ""),
        "execution_fill_ratio": row.get("execution_fill_ratio", ""),
        "execution_book_price": row.get("execution_book_price", ""),
        "execution_book_levels_used": row.get("execution_book_levels_used", ""),
        "execution_book_available_usdc": row.get("execution_book_available_usdc", ""),
        "execution_token_id": row.get("execution_token_id", ""),
        "robust_reason": row.get("robust_reason", ""),
        "worst_edge": row.get("worst_edge", ""),
        "stress_pass_rate": row.get("stress_pass_rate", ""),
        "decision_reason": row.get("decision_reason", row.get("robust_reason", "")),
        "risk_flags": row.get("risk_flags", ""),
        "visible_top_bucket": row.get("visible_top_bucket", ""),
        "visible_top_bucket_probability": row.get("visible_top_bucket_probability", ""),
        "visible_bucket_rank": row.get("visible_bucket_rank", ""),
        "visible_bucket_count": row.get("visible_bucket_count", ""),
        "fair_yes_probability": row.get("fair_yes_probability", ""),
        "fair_no_probability": row.get("fair_no_probability", ""),
        "yes_net_edge": row.get("yes_net_edge", ""),
        "no_net_edge": row.get("no_net_edge", ""),
        "expected_payout_usdc": round(expected_payout, 4),
        "expected_pnl_usdc": round(expected_payout - stake_usdc, 4),
        "prediction_c": row.get("prediction_c", ""),
        "interval_lower": row.get("interval_lower", ""),
        "interval_upper": row.get("interval_upper", ""),
        "interval_unit": row.get("interval_unit", ""),
        "end_date": row.get("end_date", ""),
        "market_has_ended": row.get("market_has_ended", "0"),
        "actual_value": "",
        "actual_status": "",
        "won": "",
        "payout_usdc": "",
        "pnl_usdc": "",
        "roi_pct": "",
    }
    payload["trade_id"] = _trade_id(payload)
    return payload


def _strategy_entry_mode(row: dict[str, Any], execution_mode: str) -> str | None:
    if execution_mode == "taker":
        return "taker"
    maker_ok = str(row.get("maker_preferred", "")) == "1" and _as_optional_float(row.get("maker_quote_price")) is not None
    if execution_mode == "maker-preferred":
        return "maker" if maker_ok else "taker"
    if execution_mode == "maker-only":
        return "maker" if maker_ok else None
    return "taker"


def _strategy_expected_roi(row: dict[str, Any], entry_mode: str) -> float | None:
    key = "maker_fill_adjusted_expected_roi" if entry_mode == "maker" else "execution_expected_roi"
    return _as_optional_float(row.get(key))


def _position_row_html(position: dict[str, Any]) -> str:
    status = str(position.get("status", ""))
    side = str(position.get("side", ""))
    pnl = position.get("pnl_usdc") if position.get("pnl_usdc") not in {"", None} else position.get("expected_pnl_usdc")
    pnl_class = _pnl_class(pnl)
    side_class = "buy_yes" if side == "BUY_YES" else "buy_no"
    actual = position.get("actual_value") or position.get("actual_status") or "pending"
    fair = _percent_from_prob(position.get("fair_probability"))
    edge = _percent_from_prob(position.get("net_edge"))
    model_main, model_sub = _model_cell(position)
    trade_tip = _trade_tooltip(position)
    actual_text = _actual_text(position, actual)
    compact_title = _compact_event_title(str(position.get("event_title", "")))
    price_sub = _entry_price_sub(position)
    return (
        f'<tr data-status="{html.escape(status)}">'
        f'<td><span class="chip {html.escape(status)}">{html.escape(status)}</span></td>'
        f'<td class="trade-cell has-tip" data-tip="{_tooltip_attr(trade_tip)}">'
        f'<div class="trade-main"><span class="{side_class}">{html.escape(side)}</span>'
        f'<span>{html.escape(str(position.get("group_item_title", "")))}</span></div>'
        f'<div class="trade-sub">{html.escape(compact_title)}</div>'
        '</td>'
        f'<td class="num">{html.escape(_money(position.get("stake_usdc")))}</td>'
        f'<td class="num">{html.escape(_num(position.get("price")))}<div class="cell-sub">{html.escape(price_sub)}</div></td>'
        f'<td class="num">{html.escape(fair)}<div class="cell-sub">{html.escape(edge)}</div></td>'
        f'<td><strong>{html.escape(model_main)}</strong><div class="cell-sub">{html.escape(model_sub)}</div></td>'
        f'<td class="num {pnl_class}">{html.escape(_money(pnl))}</td>'
        f'<td>{html.escape(actual_text)}</td>'
        "</tr>"
    )


def _candidate_stake(row: dict[str, Any], bankroll_usdc: float, max_stake_usdc: float) -> float:
    suggested = _as_float(row.get("suggested_max_stake_usdc"))
    stake = suggested if suggested > 0 else bankroll_usdc * 0.0025
    return round(min(stake, max_stake_usdc), 4)


def _contains_interval(value: float, lower: float | None, upper: float | None) -> bool:
    if lower is not None and value < lower:
        return False
    if upper is not None and value >= upper:
        return False
    return True


def _trade_id(position: dict[str, Any]) -> str:
    raw = "|".join(
        str(position.get(key, ""))
        for key in ("opened_at", "event_slug", "market_slug", "side", "stake_usdc", "price")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _model_top_text(position: dict[str, Any]) -> str:
    bucket = str(position.get("visible_top_bucket") or "-")
    probability = _percent_from_prob(position.get("visible_top_bucket_probability"))
    rank = position.get("visible_bucket_rank") or "-"
    count = position.get("visible_bucket_count") or "-"
    if probability == "-":
        return f"{bucket} · rank {rank}/{count}"
    return f"{bucket} · {probability} · rank {rank}/{count}"


def _model_cell(position: dict[str, Any]) -> tuple[str, str]:
    bucket = str(position.get("visible_top_bucket") or "-")
    probability = _percent_from_prob(position.get("visible_top_bucket_probability"))
    rank = position.get("visible_bucket_rank") or "-"
    count = position.get("visible_bucket_count") or "-"
    prediction = _num(position.get("prediction_c"))
    label = bucket if probability == "-" else f"{bucket} {probability}"
    return label, f"pred {prediction}C · {rank}/{count}"


def _decision_text(position: dict[str, Any]) -> str:
    parts = [str(position.get("decision_reason") or "").strip()]
    risk_flags = str(position.get("risk_flags") or "").strip()
    if risk_flags:
        parts.append(f"Risk: {risk_flags}")
    return " | ".join(part for part in parts if part) or "-"


def _entry_price_sub(position: dict[str, Any]) -> str:
    mode = str(position.get("entry_mode") or "taker")
    if mode == "maker":
        fill = _percent_from_prob(position.get("maker_fill_score"))
        return f"maker · fill {fill}"
    depth_fill = _percent_from_prob(position.get("execution_fill_ratio"))
    quality = str(position.get("execution_quality") or "").strip()
    if depth_fill != "-":
        return f"taker · {quality or 'depth'} · fill {depth_fill}"
    return f"taker · {quality}" if quality else "taker"


def _trade_tooltip(position: dict[str, Any]) -> str:
    lines = [
        f"Рынок: {position.get('event_title') or '-'}",
        f"Outcome: {position.get('group_item_title') or '-'}",
        f"Сторона: {position.get('side') or '-'}",
        f"Вход: {_entry_price_sub(position)}",
        f"Модель top: {_model_top_text(position)}",
        f"Fair / Edge: {_percent_from_prob(position.get('fair_probability'))} / {_percent_from_prob(position.get('net_edge'))}",
        f"Shares: {_num(position.get('shares'))}",
        f"Почему выбрано: {_decision_text(position)}",
    ]
    if str(position.get("execution_fill_ratio") or "").strip():
        lines.append(
            "Стакан: "
            f"fill {_percent_from_prob(position.get('execution_fill_ratio'))}, "
            f"avg {_num(position.get('execution_book_price'))}, "
            f"levels {position.get('execution_book_levels_used') or '-'}"
        )
    robust_reason = str(position.get("robust_reason") or "").strip()
    if robust_reason:
        lines.append(f"Robust: {robust_reason}")
    maker_reason = str(position.get("maker_reason") or "").strip()
    if maker_reason:
        lines.append(f"Maker: {maker_reason}")
    strategy = str(position.get("strategy_version") or "").strip()
    if strategy:
        lines.append(f"Strategy: {strategy}")
    return "\n".join(lines)


def _compact_event_title(title: str) -> str:
    match = re.match(r"(Highest|Lowest) temperature in (.+?) on (.+?)\?", title)
    if not match:
        return title
    extreme, city, date_label = match.groups()
    prefix = "High" if extreme == "Highest" else "Low"
    return f"{prefix} · {city} · {date_label}"


def _actual_text(position: dict[str, Any], actual: Any) -> str:
    if actual in {"", None, "pending", "missing"}:
        return str(actual or "pending")
    unit = "F" if position.get("interval_unit") == "fahrenheit" else "C"
    return f"{_num(actual)}{unit}"


def _tooltip_attr(value: str) -> str:
    return html.escape(value, quote=True)


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv(records: list[dict[str, Any]], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        if not records:
            fh.write("")
            return
        fieldnames = []
        seen = set()
        for record in records:
            for key in record:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def _write_json(payload: dict[str, Any], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _as_float(value: Any, default: float = 0.0) -> float:
    if value in {"", None}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _money(value: Any) -> str:
    parsed = _as_optional_float(value)
    return "-" if parsed is None else f"${parsed:,.2f}"


def _num(value: Any) -> str:
    parsed = _as_optional_float(value)
    if parsed is None:
        return "-"
    if abs(parsed) >= 100:
        return f"{parsed:,.1f}"
    return f"{parsed:.4f}".rstrip("0").rstrip(".")


def _percent(value: Any) -> str:
    parsed = _as_optional_float(value)
    return "-" if parsed is None else f"{parsed:.1f}%"


def _percent_from_prob(value: Any) -> str:
    parsed = _as_optional_float(value)
    return "-" if parsed is None else f"{parsed * 100:.1f}%"


def _pnl_class(value: Any) -> str:
    parsed = _as_optional_float(value)
    if parsed is None or parsed == 0:
        return ""
    return "positive" if parsed > 0 else "negative"
