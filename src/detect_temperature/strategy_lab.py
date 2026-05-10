from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signals import fee_per_share, normal_interval_probability
from .units import celsius_to_fahrenheit


@dataclass(frozen=True)
class StressScenario:
    name: str
    mean_shift_c: float
    sigma_c: float
    extra_slippage: float


def run_strategy_lab(
    signals_path: str | Path,
    candidates_output_path: str | Path,
    portfolio_output_path: str | Path | None = None,
    summary_output_path: str | Path | None = None,
    report_path: str | Path | None = None,
    orderbooks_path: str | Path | None = None,
    bankroll_usdc: float = 1000.0,
    max_positions: int = 100,
    max_stake_usdc: float = 5.0,
    max_total_exposure_pct: float = 0.5,
    max_event_exposure_pct: float = 0.05,
    max_event_positions: int = 2,
    max_city_positions: int = 4,
    max_city_exposure_pct: float = 0.08,
    max_date_exposure_pct: float = 0.30,
    max_extreme_exposure_pct: float = 0.35,
    min_price: float = 0.005,
    max_price: float = 0.97,
    robust_min_edge: float = 0.01,
    min_scenario_pass_rate: float = 1.0,
    weather_fee_rate: float = 0.05,
    max_execution_slippage: float = 0.02,
    maker_quote_improvement: float = 0.005,
    maker_min_fill_score: float = 0.35,
    maker_adverse_selection_penalty: float = 0.01,
    mean_shifts_c: tuple[float, ...] = (-1.0, 0.0, 1.0),
    sigma_values_c: tuple[float, ...] = (1.5, 2.0, 2.5),
    slippage_values: tuple[float, ...] = (0.0, 0.01),
) -> dict[str, Any]:
    signals = _read_csv(signals_path)
    orderbooks = _load_orderbooks(orderbooks_path) if orderbooks_path else {}
    scenarios = _stress_scenarios(mean_shifts_c, sigma_values_c, slippage_values)
    candidates = [
        _candidate_row(
            signal,
            scenarios,
            orderbooks=orderbooks,
            bankroll_usdc=bankroll_usdc,
            max_stake_usdc=max_stake_usdc,
            robust_min_edge=robust_min_edge,
            weather_fee_rate=weather_fee_rate,
            max_execution_slippage=max_execution_slippage,
            maker_quote_improvement=maker_quote_improvement,
            maker_min_fill_score=maker_min_fill_score,
            maker_adverse_selection_penalty=maker_adverse_selection_penalty,
        )
        for signal in signals
        if signal.get("paper_side") in {"BUY_YES", "BUY_NO"}
        and _as_float(signal.get("paper_price")) is not None
        and min_price <= (_as_float(signal.get("paper_price")) or -1.0) <= max_price
        and signal.get("market_has_ended") != "1"
    ]
    candidates = [row for row in candidates if row is not None]
    selected = _select_portfolio(
        candidates,
        bankroll_usdc=bankroll_usdc,
        max_positions=max_positions,
        max_stake_usdc=max_stake_usdc,
        max_total_exposure_pct=max_total_exposure_pct,
        max_event_exposure_pct=max_event_exposure_pct,
        max_event_positions=max_event_positions,
        max_city_positions=max_city_positions,
        max_city_exposure_pct=max_city_exposure_pct,
        max_date_exposure_pct=max_date_exposure_pct,
        max_extreme_exposure_pct=max_extreme_exposure_pct,
        min_scenario_pass_rate=min_scenario_pass_rate,
    )
    selected_ids = {row["candidate_id"] for row in selected}
    for row in candidates:
        row["selected"] = int(row["candidate_id"] in selected_ids)

    summary = _summary(
        signals=signals,
        candidates=candidates,
        selected=selected,
        scenarios=scenarios,
        bankroll_usdc=bankroll_usdc,
        robust_min_edge=robust_min_edge,
        min_scenario_pass_rate=min_scenario_pass_rate,
        max_event_positions=max_event_positions,
        max_city_positions=max_city_positions,
        max_city_exposure_pct=max_city_exposure_pct,
        max_date_exposure_pct=max_date_exposure_pct,
        max_extreme_exposure_pct=max_extreme_exposure_pct,
        max_execution_slippage=max_execution_slippage,
        maker_quote_improvement=maker_quote_improvement,
        maker_min_fill_score=maker_min_fill_score,
        maker_adverse_selection_penalty=maker_adverse_selection_penalty,
        orderbooks_path=str(orderbooks_path) if orderbooks_path else "",
        orderbook_count=len(orderbooks),
    )
    payload = {"summary": summary, "candidates": candidates, "selected": selected}

    _write_csv(candidates, candidates_output_path)
    if portfolio_output_path:
        _write_csv(selected, portfolio_output_path)
    if summary_output_path:
        _write_json({"summary": summary}, summary_output_path)
    if report_path:
        render_strategy_lab_report(payload, report_path)
    return payload


def render_strategy_lab_report(payload: dict[str, Any], path: str | Path) -> None:
    summary = payload["summary"]
    candidates = payload["candidates"]
    selected = payload["selected"]
    scenario_items = "".join(f"<li>{html.escape(item)}</li>" for item in summary["scenarios"])
    concentration_rows = _concentration_html(summary["selected_concentration"])
    selected_rows = "\n".join(_candidate_html(row) for row in selected[:120])
    rejected_rows = "\n".join(
        _candidate_html(row)
        for row in sorted(
            [row for row in candidates if not row.get("robust_pass")],
            key=lambda row: _as_float(row.get("base_edge"), -999.0),
            reverse=True,
        )[:80]
    )
    document = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Strategy Lab</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #fff;
      --text: #17202a;
      --muted: #637083;
      --line: #d8dee8;
      --good: #0f7a45;
      --bad: #b42318;
      --accent: #0f766e;
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
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0; font-size: 20px; }}
    h2 {{ margin: 18px 0 10px; font-size: 16px; }}
    main {{ padding: 18px 24px 28px; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 21px; }}
    .note {{
      color: var(--muted);
      max-width: 980px;
      margin: 8px 0 14px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 14px;
    }}
    .table-wrap {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
    }}
    table {{ width: 100%; min-width: 980px; border-collapse: collapse; }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }}
    th {{ color: var(--muted); font-size: 12px; background: #fafbfc; position: sticky; top: 0; }}
    td.title {{ white-space: normal; min-width: 260px; }}
    .good {{ color: var(--good); font-weight: 650; }}
    .bad {{ color: var(--bad); font-weight: 650; }}
    .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
  <header>
    <h1>Strategy Lab: Robust Filter + Portfolio Optimizer + Maker Paper Mode</h1>
    <div class="note">Это не новый live-бот. Это стресс-тест текущих v2 сигналов и оптимизация портфеля: остается ли edge, если прогноз сместился, sigma шире, исполнение хуже, а концентрация по городу/дате ограничена.</div>
  </header>
  <main>
    <div class="metrics">
      {_metric("Trade candidates", summary["trade_candidates"])}
      {_metric("Robust pass", summary["robust_pass"])}
      {_metric("Selected", summary["selected_positions"])}
      {_metric("Selected stake", _money(summary["selected_stake_usdc"]))}
      {_metric("Base expected PnL", _money(summary["selected_base_expected_pnl_usdc"]))}
      {_metric("Exec expected PnL", _money(summary["selected_execution_expected_pnl_usdc"]))}
      {_metric("Maker fill-adj PnL", _money(summary["selected_maker_fill_adjusted_expected_pnl_usdc"]))}
      {_metric("Maker eligible", summary["selected_maker_eligible"])}
      {_metric("Maker preferred", summary["selected_maker_preferred"])}
      {_metric("Orderbooks", summary.get("orderbook_count", 0))}
      {_metric("Fillable", summary.get("selected_execution_fillable", 0))}
      {_metric("Worst edge min", _percent(summary["selected_worst_edge_min"]))}
      {_metric("Exec slip max", _percent(summary["selected_execution_slippage_max"]))}
      {_metric("Events", summary["selected_events"])}
      {_metric("Max city stake", _money(summary["selected_max_city_stake_usdc"]))}
      {_metric("Max date stake", _money(summary["selected_max_date_stake_usdc"]))}
    </div>
    <section class="panel">
      <strong>Stress scenarios</strong>
      <ul>{scenario_items}</ul>
      <p class="note">`Worst edge` - худший edge по всем сценариям уже после estimated slippage. `Exec expected PnL` считает taker-вход после execution penalty. Если подключен CLOB orderbook snapshot, Strategy Lab также проверяет, хватило бы ask-depth на planned stake. `Maker fill-adj PnL` считает лимитный вход с fill score и adverse-selection penalty; это гипотеза, а не гарантия исполнения.</p>
    </section>

    <h2>Concentration</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Type</th><th>Name</th><th>Positions</th><th>Stake</th><th>Share of bankroll</th></tr></thead>
        <tbody>{concentration_rows}</tbody>
      </table>
    </div>

    <h2>Selected robust portfolio</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Side</th><th>Outcome</th><th>Taker</th><th>Maker</th><th>Fill</th><th>Exec edge</th><th>Maker edge</th><th>Worst edge</th><th>Stake</th><th>Exec/Maker PnL</th><th>Market</th></tr></thead>
        <tbody>{selected_rows}</tbody>
      </table>
    </div>

    <h2>Rejected high-base-edge examples</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Side</th><th>Outcome</th><th>Taker</th><th>Maker</th><th>Fill</th><th>Exec edge</th><th>Maker edge</th><th>Worst edge</th><th>Stake</th><th>Exec/Maker PnL</th><th>Market</th></tr></thead>
        <tbody>{rejected_rows}</tbody>
      </table>
    </div>
  </main>
</body>
</html>
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(document, encoding="utf-8")


def _candidate_row(
    signal: dict[str, Any],
    scenarios: list[StressScenario],
    orderbooks: dict[str, dict[str, Any]],
    bankroll_usdc: float,
    max_stake_usdc: float,
    robust_min_edge: float,
    weather_fee_rate: float,
    max_execution_slippage: float,
    maker_quote_improvement: float,
    maker_min_fill_score: float,
    maker_adverse_selection_penalty: float,
) -> dict[str, Any] | None:
    side = signal.get("paper_side", "")
    prediction_c = _as_float(signal.get("prediction_c"))
    price = _as_float(signal.get("paper_price"))
    lower = _as_optional_float(signal.get("interval_lower"))
    upper = _as_optional_float(signal.get("interval_upper"))
    unit = signal.get("interval_unit") or "celsius"
    if prediction_c is None or price is None:
        return None
    planned_stake = _candidate_stake(signal, bankroll_usdc=bankroll_usdc, max_stake_usdc=max_stake_usdc)
    execution = _execution_profile(
        signal,
        side=side,
        quoted_price=price,
        orderbooks=orderbooks,
        planned_stake_usdc=planned_stake,
        weather_fee_rate=weather_fee_rate,
    )
    base_fair = _as_float(signal.get("paper_fair_probability"))
    base_edge = _as_float(signal.get("paper_net_edge"))
    base_fee = fee_per_share(price, weather_fee_rate)
    base_expected_roi = (base_fair / (price + base_fee) - 1.0) if base_fair is not None and price + base_fee > 0 else None
    execution_price = min(0.999, max(0.001, price + execution["estimated_slippage"]))
    execution_fee = fee_per_share(execution_price, weather_fee_rate)
    execution_base_edge = base_fair - execution_price - execution_fee if base_fair is not None else None
    execution_expected_roi = (
        base_fair / (execution_price + execution_fee) - 1.0
        if base_fair is not None and execution_price + execution_fee > 0
        else None
    )
    maker = _maker_profile(
        signal,
        side=side,
        taker_price=price,
        taker_expected_roi=execution_expected_roi,
        fair_probability=base_fair,
        scenarios=scenarios,
        prediction_c=prediction_c,
        interval_unit=unit,
        lower=lower,
        upper=upper,
        quote_improvement=maker_quote_improvement,
        min_fill_score=maker_min_fill_score,
        adverse_selection_penalty=maker_adverse_selection_penalty,
    )

    stress_rows = [
        _stress_result(
            side=side,
            prediction_c=prediction_c,
            interval_unit=unit,
            lower=lower,
            upper=upper,
            price=price,
            execution_slippage=execution["estimated_slippage"],
            scenario=scenario,
            weather_fee_rate=weather_fee_rate,
        )
        for scenario in scenarios
    ]
    edges = [row["edge"] for row in stress_rows]
    fair_values = [row["fair_probability"] for row in stress_rows]
    passing = sum(edge >= robust_min_edge for edge in edges)
    pass_rate = passing / len(edges) if edges else 0.0
    worst_edge = min(edges) if edges else None
    mean_edge = sum(edges) / len(edges) if edges else None
    sensitivity = (base_edge - worst_edge) if base_edge is not None and worst_edge is not None else None
    robust_score = (worst_edge or 0.0) + pass_rate * 0.01 - max(sensitivity or 0.0, 0.0) * 0.1
    execution_ok = execution["estimated_slippage"] <= max_execution_slippage and execution["fillable"]
    robust_pass = int(bool(edges and passing == len(edges) and (worst_edge or -999.0) >= robust_min_edge and execution_ok))
    context = _market_context(signal.get("event_title", ""), signal.get("event_slug", ""))
    return {
        "candidate_id": _candidate_id(signal),
        "event_slug": signal.get("event_slug", ""),
        "event_title": signal.get("event_title", ""),
        "market_city": context["city"],
        "market_date": context["date"],
        "market_extreme": context["extreme"],
        "market_slug": signal.get("market_slug", ""),
        "group_item_title": signal.get("group_item_title", ""),
        "side": side,
        "price": _round(price),
        "base_fair_probability": _round_or_none(base_fair),
        "base_edge": _round_or_none(base_edge),
        "base_expected_roi": _round_or_none(base_expected_roi),
        "execution_price": _round(execution_price),
        "execution_slippage": _round(execution["estimated_slippage"]),
        "execution_fillable": int(bool(execution["fillable"])),
        "execution_fill_ratio": _round(execution["fill_ratio"]),
        "execution_book_price": _round_or_none(execution["book_price"]),
        "execution_book_levels_used": execution["book_levels_used"],
        "execution_book_available_usdc": _round(execution["book_available_usdc"]),
        "execution_token_id": execution["token_id"],
        "execution_quality": execution["quality"],
        "execution_flags": "; ".join(execution["flags"]),
        "execution_base_edge": _round_or_none(execution_base_edge),
        "execution_expected_roi": _round_or_none(execution_expected_roi),
        "maker_quote_price": _round_or_none(maker["quote_price"]),
        "maker_price_improvement": _round_or_none(maker["price_improvement"]),
        "maker_fill_score": _round_or_none(maker["fill_score"]),
        "maker_eligible": int(bool(maker["eligible"])),
        "maker_edge_if_filled": _round_or_none(maker["edge_if_filled"]),
        "maker_worst_edge": _round_or_none(maker["worst_edge"]),
        "maker_expected_roi_if_filled": _round_or_none(maker["expected_roi_if_filled"]),
        "maker_fill_adjusted_expected_roi": _round_or_none(maker["fill_adjusted_expected_roi"]),
        "maker_preferred": int(bool(maker["preferred"] and maker["eligible"])),
        "maker_reason": maker["reason"],
        "maker_adverse_selection_penalty": maker_adverse_selection_penalty,
        "quote_spread": _round_or_none(_as_optional_float(signal.get("spread"))),
        "quote_liquidity": _round_or_none(_as_optional_float(signal.get("liquidity"))),
        "quote_market_volume": _round_or_none(_as_optional_float(signal.get("market_volume"))),
        "worst_edge": _round_or_none(worst_edge),
        "mean_edge": _round_or_none(mean_edge),
        "best_edge": _round_or_none(max(edges) if edges else None),
        "min_stress_fair_probability": _round_or_none(min(fair_values) if fair_values else None),
        "max_stress_fair_probability": _round_or_none(max(fair_values) if fair_values else None),
        "stress_pass_rate": _round(pass_rate),
        "negative_scenarios": sum(edge < 0 for edge in edges),
        "failed_scenarios": len(edges) - passing,
        "robust_min_edge": robust_min_edge,
        "robust_pass": robust_pass,
        "robust_score": _round(robust_score),
        "sensitivity_to_stress": _round_or_none(sensitivity),
        "worst_scenario": min(stress_rows, key=lambda row: row["edge"])["scenario"] if stress_rows else "",
        "stress_summary": _stress_summary(stress_rows),
        "robust_reason": (
            "passes all stress and execution checks"
            if robust_pass
            else _robust_reject_reason(stress_rows, robust_min_edge, execution, max_execution_slippage)
        ),
        "prediction_c": _round(prediction_c),
        "interval_lower": signal.get("interval_lower", ""),
        "interval_upper": signal.get("interval_upper", ""),
        "interval_unit": unit,
        "visible_top_bucket": signal.get("visible_top_bucket", ""),
        "visible_bucket_rank": signal.get("visible_bucket_rank", ""),
        "visible_bucket_count": signal.get("visible_bucket_count", ""),
        "risk_flags": signal.get("risk_flags", ""),
        "decision_reason": signal.get("decision_reason", signal.get("reason", "")),
        "market_has_ended": signal.get("market_has_ended", ""),
        "suggested_max_stake_usdc": signal.get("suggested_max_stake_usdc", ""),
        "planned_stake_usdc": planned_stake,
        "selected": 0,
        "stake_usdc": "",
        "base_expected_pnl_usdc": "",
        "execution_expected_pnl_usdc": "",
    }


def _stress_result(
    side: str,
    prediction_c: float,
    interval_unit: str,
    lower: float | None,
    upper: float | None,
    price: float,
    execution_slippage: float,
    scenario: StressScenario,
    weather_fee_rate: float,
) -> dict[str, Any]:
    shifted_c = prediction_c + scenario.mean_shift_c
    mean = celsius_to_fahrenheit(shifted_c) if interval_unit == "fahrenheit" else shifted_c
    sigma = scenario.sigma_c * 9.0 / 5.0 if interval_unit == "fahrenheit" else scenario.sigma_c
    fair_yes = normal_interval_probability(mean, sigma, lower, upper)
    fair = fair_yes if side == "BUY_YES" else 1.0 - fair_yes
    stress_price = min(0.999, max(0.001, price + execution_slippage + scenario.extra_slippage))
    fee = fee_per_share(stress_price, weather_fee_rate)
    edge = fair - stress_price - fee
    expected_roi = fair / (stress_price + fee) - 1.0 if stress_price + fee > 0 else None
    return {
        "scenario": scenario.name,
        "fair_probability": fair,
        "price": stress_price,
        "edge": edge,
        "expected_roi": expected_roi,
    }


def _select_portfolio(
    candidates: list[dict[str, Any]],
    bankroll_usdc: float,
    max_positions: int,
    max_stake_usdc: float,
    max_total_exposure_pct: float,
    max_event_exposure_pct: float,
    max_event_positions: int,
    max_city_positions: int,
    max_city_exposure_pct: float,
    max_date_exposure_pct: float,
    max_extreme_exposure_pct: float,
    min_scenario_pass_rate: float,
) -> list[dict[str, Any]]:
    total_cap = bankroll_usdc * max_total_exposure_pct
    event_cap = bankroll_usdc * max_event_exposure_pct
    city_cap = bankroll_usdc * max_city_exposure_pct
    date_cap = bankroll_usdc * max_date_exposure_pct
    extreme_cap = bankroll_usdc * max_extreme_exposure_pct
    total_exposure = 0.0
    event_exposure: dict[str, float] = {}
    city_exposure: dict[str, float] = {}
    date_exposure: dict[str, float] = {}
    extreme_exposure: dict[str, float] = {}
    event_positions: dict[str, int] = {}
    city_positions: dict[str, int] = {}
    date_positions: dict[str, int] = {}
    extreme_positions: dict[str, int] = {}
    selected = []
    rows = [
        row for row in candidates
        if row.get("robust_pass") == 1
        and _as_float(row.get("stress_pass_rate"), 0.0) >= min_scenario_pass_rate
    ]
    remaining = list(rows)
    while remaining and len(selected) < max_positions:
        scored = [
            (
                _diversified_score(
                    row,
                    city_positions=city_positions,
                    date_exposure=date_exposure,
                    city_cap=city_cap,
                    date_cap=date_cap,
                ),
                row,
            )
            for row in remaining
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        picked: dict[str, Any] | None = None
        for _, row in scored:
            if _fits_caps(
                row,
                stake=_candidate_stake(row, bankroll_usdc=bankroll_usdc, max_stake_usdc=max_stake_usdc),
                total_exposure=total_exposure,
                total_cap=total_cap,
                event_exposure=event_exposure,
                event_cap=event_cap,
                event_positions=event_positions,
                max_event_positions=max_event_positions,
                city_exposure=city_exposure,
                city_cap=city_cap,
                city_positions=city_positions,
                max_city_positions=max_city_positions,
                date_exposure=date_exposure,
                date_cap=date_cap,
                extreme_exposure=extreme_exposure,
                extreme_cap=extreme_cap,
            ):
                picked = row
                break
        if picked is None:
            break
        row = picked
        remaining.remove(row)
        if len(selected) >= max_positions:
            break
        event_slug = row.get("event_slug", "")
        city = row.get("market_city", "")
        date = row.get("market_date", "")
        extreme = row.get("market_extreme", "")
        stake = _candidate_stake(row, bankroll_usdc=bankroll_usdc, max_stake_usdc=max_stake_usdc)
        selected_row = dict(row)
        selected_row["selected"] = 1
        selected_row["stake_usdc"] = stake
        selected_row["base_expected_pnl_usdc"] = _round(stake * (_as_float(row.get("base_expected_roi"), 0.0)))
        selected_row["execution_expected_pnl_usdc"] = _round(
            stake * (_as_float(row.get("execution_expected_roi"), 0.0))
        )
        selected_row["maker_expected_pnl_if_filled_usdc"] = _round(
            stake * (_as_float(row.get("maker_expected_roi_if_filled"), 0.0))
        )
        selected_row["maker_fill_adjusted_expected_pnl_usdc"] = _round(
            stake * (_as_float(row.get("maker_fill_adjusted_expected_roi"), 0.0))
        )
        selected.append(selected_row)
        total_exposure += stake
        event_exposure[event_slug] = event_exposure.get(event_slug, 0.0) + stake
        city_exposure[city] = city_exposure.get(city, 0.0) + stake
        date_exposure[date] = date_exposure.get(date, 0.0) + stake
        extreme_exposure[extreme] = extreme_exposure.get(extreme, 0.0) + stake
        event_positions[event_slug] = event_positions.get(event_slug, 0) + 1
        city_positions[city] = city_positions.get(city, 0) + 1
        date_positions[date] = date_positions.get(date, 0) + 1
        extreme_positions[extreme] = extreme_positions.get(extreme, 0) + 1
    return selected


def _fits_caps(
    row: dict[str, Any],
    stake: float,
    total_exposure: float,
    total_cap: float,
    event_exposure: dict[str, float],
    event_cap: float,
    event_positions: dict[str, int],
    max_event_positions: int,
    city_exposure: dict[str, float],
    city_cap: float,
    city_positions: dict[str, int],
    max_city_positions: int,
    date_exposure: dict[str, float],
    date_cap: float,
    extreme_exposure: dict[str, float],
    extreme_cap: float,
) -> bool:
    event_slug = row.get("event_slug", "")
    city = row.get("market_city", "")
    date = row.get("market_date", "")
    extreme = row.get("market_extreme", "")
    if total_exposure + stake > total_cap:
        return False
    if event_positions.get(event_slug, 0) >= max_event_positions:
        return False
    if event_exposure.get(event_slug, 0.0) + stake > event_cap:
        return False
    if city_positions.get(city, 0) >= max_city_positions:
        return False
    if city_exposure.get(city, 0.0) + stake > city_cap:
        return False
    if date_exposure.get(date, 0.0) + stake > date_cap:
        return False
    if extreme_exposure.get(extreme, 0.0) + stake > extreme_cap:
        return False
    return True


def _diversified_score(
    row: dict[str, Any],
    city_positions: dict[str, int],
    date_exposure: dict[str, float],
    city_cap: float,
    date_cap: float,
) -> float:
    city = row.get("market_city", "")
    date = row.get("market_date", "")
    raw = _as_float(row.get("robust_score"), -999.0) or -999.0
    city_penalty = 0.012 * city_positions.get(city, 0)
    date_penalty = 0.01 * (date_exposure.get(date, 0.0) / date_cap) if date_cap > 0 else 0.0
    cap_bonus = 0.004 if city_positions.get(city, 0) == 0 else 0.0
    return raw - city_penalty - date_penalty + cap_bonus


def _summary(
    signals: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    scenarios: list[StressScenario],
    bankroll_usdc: float,
    robust_min_edge: float,
    min_scenario_pass_rate: float,
    max_event_positions: int,
    max_city_positions: int,
    max_city_exposure_pct: float,
    max_date_exposure_pct: float,
    max_extreme_exposure_pct: float,
    max_execution_slippage: float,
    maker_quote_improvement: float,
    maker_min_fill_score: float,
    maker_adverse_selection_penalty: float,
    orderbooks_path: str = "",
    orderbook_count: int = 0,
) -> dict[str, Any]:
    trade_rows = [row for row in signals if row.get("paper_side") in {"BUY_YES", "BUY_NO"}]
    robust = [row for row in candidates if row.get("robust_pass") == 1]
    selected_worst_edges = [_as_float(row.get("worst_edge")) for row in selected]
    selected_worst_edges = [edge for edge in selected_worst_edges if edge is not None]
    selected_base_pnl = sum(_as_float(row.get("base_expected_pnl_usdc")) for row in selected)
    selected_execution_pnl = sum(_as_float(row.get("execution_expected_pnl_usdc")) for row in selected)
    selected_maker_if_filled_pnl = sum(_as_float(row.get("maker_expected_pnl_if_filled_usdc")) for row in selected)
    selected_maker_fill_adjusted_pnl = sum(
        _as_float(row.get("maker_fill_adjusted_expected_pnl_usdc")) for row in selected
    )
    selected_stake = sum(_as_float(row.get("stake_usdc")) for row in selected)
    selected_slippage = [_as_float(row.get("execution_slippage")) for row in selected]
    selected_slippage = [value for value in selected_slippage if value is not None]
    selected_fill_ratios = [_as_float(row.get("execution_fill_ratio")) for row in selected]
    selected_fill_ratios = [value for value in selected_fill_ratios if value is not None]
    selected_fill_scores = [_as_float(row.get("maker_fill_score")) for row in selected if row.get("maker_eligible") == 1]
    selected_fill_scores = [value for value in selected_fill_scores if value is not None]
    concentration = _concentration(selected, bankroll_usdc=bankroll_usdc)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bankroll_usdc": bankroll_usdc,
        "signals": len(signals),
        "trade_candidates": len(trade_rows),
        "stress_evaluated_candidates": len(candidates),
        "robust_pass": len(robust),
        "robust_pass_pct": _round(len(robust) / len(candidates) * 100.0) if candidates else 0.0,
        "selected_positions": len(selected),
        "selected_events": len({row.get("event_slug") for row in selected}),
        "selected_stake_usdc": _round(selected_stake),
        "selected_base_expected_pnl_usdc": _round(selected_base_pnl),
        "selected_base_expected_roi_pct": _round(selected_base_pnl / selected_stake * 100.0) if selected_stake else 0.0,
        "selected_execution_expected_pnl_usdc": _round(selected_execution_pnl),
        "selected_execution_expected_roi_pct": _round(selected_execution_pnl / selected_stake * 100.0) if selected_stake else 0.0,
        "selected_maker_expected_pnl_if_filled_usdc": _round(selected_maker_if_filled_pnl),
        "selected_maker_fill_adjusted_expected_pnl_usdc": _round(selected_maker_fill_adjusted_pnl),
        "selected_maker_fill_adjusted_roi_pct": _round(selected_maker_fill_adjusted_pnl / selected_stake * 100.0) if selected_stake else 0.0,
        "selected_maker_eligible": sum(1 for row in selected if row.get("maker_eligible") == 1),
        "selected_maker_preferred": sum(1 for row in selected if row.get("maker_preferred") == 1),
        "selected_maker_fill_score_mean": _round_or_none(sum(selected_fill_scores) / len(selected_fill_scores) if selected_fill_scores else None),
        "selected_execution_slippage_mean": _round_or_none(sum(selected_slippage) / len(selected_slippage) if selected_slippage else None),
        "selected_execution_slippage_max": _round_or_none(max(selected_slippage) if selected_slippage else None),
        "selected_execution_quality": _quality_counts(selected),
        "selected_execution_fillable": sum(1 for row in selected if row.get("execution_fillable") == 1),
        "selected_execution_fill_ratio_mean": _round_or_none(sum(selected_fill_ratios) / len(selected_fill_ratios) if selected_fill_ratios else None),
        "selected_worst_edge_min": _round_or_none(min(selected_worst_edges) if selected_worst_edges else None),
        "selected_worst_edge_mean": _round_or_none(sum(selected_worst_edges) / len(selected_worst_edges) if selected_worst_edges else None),
        "selected_max_city_positions": _max_count(concentration["city"]),
        "selected_max_city_stake_usdc": _max_stake(concentration["city"]),
        "selected_max_date_stake_usdc": _max_stake(concentration["date"]),
        "selected_max_extreme_stake_usdc": _max_stake(concentration["extreme"]),
        "selected_concentration": concentration,
        "robust_min_edge": robust_min_edge,
        "min_scenario_pass_rate": min_scenario_pass_rate,
        "max_event_positions": max_event_positions,
        "max_city_positions": max_city_positions,
        "max_city_exposure_pct": max_city_exposure_pct,
        "max_date_exposure_pct": max_date_exposure_pct,
        "max_extreme_exposure_pct": max_extreme_exposure_pct,
        "max_execution_slippage": max_execution_slippage,
        "maker_quote_improvement": maker_quote_improvement,
        "maker_min_fill_score": maker_min_fill_score,
        "maker_adverse_selection_penalty": maker_adverse_selection_penalty,
        "orderbooks_path": orderbooks_path,
        "orderbook_count": orderbook_count,
        "scenarios": [scenario.name for scenario in scenarios],
    }


def _stress_scenarios(
    mean_shifts_c: tuple[float, ...],
    sigma_values_c: tuple[float, ...],
    slippage_values: tuple[float, ...],
) -> list[StressScenario]:
    return [
        StressScenario(
            name=f"shift={shift:+.1f}C sigma={sigma:.1f}C slip={slip:.3f}",
            mean_shift_c=shift,
            sigma_c=sigma,
            extra_slippage=slip,
        )
        for shift in mean_shifts_c
        for sigma in sigma_values_c
        for slip in slippage_values
    ]


def _load_orderbooks(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    books = payload.get("books", payload) if isinstance(payload, dict) else payload
    if not isinstance(books, list):
        return {}
    result = {}
    for book in books:
        if not isinstance(book, dict):
            continue
        token_id = str(book.get("asset_id") or book.get("asset") or book.get("token_id") or "").strip()
        if token_id:
            result[token_id] = book
    return result


def _token_id_for_side(signal: dict[str, Any], side: str) -> str:
    key = "yes_token_id" if side == "BUY_YES" else "no_token_id"
    return str(signal.get(key) or "").strip()


def _orderbook_depth_estimate(
    book: dict[str, Any] | None,
    quoted_price: float,
    planned_stake_usdc: float,
    weather_fee_rate: float,
) -> dict[str, Any]:
    if not book:
        return {
            "missing_book": True,
            "fillable": False,
            "fill_ratio": 0.0,
            "average_price": None,
            "levels_used": 0,
            "available_usdc": 0.0,
        }

    fee = fee_per_share(quoted_price, weather_fee_rate)
    target_shares = planned_stake_usdc / (quoted_price + fee) if quoted_price + fee > 0 else 0.0
    remaining = target_shares
    filled = 0.0
    notional = 0.0
    levels_used = 0
    available_usdc = 0.0
    asks = sorted(
        (_book_level(level) for level in book.get("asks") or []),
        key=lambda item: item[0],
    )
    asks = [(price, size) for price, size in asks if price is not None and size is not None and price > 0 and size > 0]
    for price, size in asks:
        available_usdc += price * size
        if remaining <= 0:
            continue
        take = min(size, remaining)
        filled += take
        notional += take * price
        remaining -= take
        levels_used += 1

    fill_ratio = (filled / target_shares) if target_shares > 0 else 0.0
    average_price = (notional / filled) if filled > 0 else None
    return {
        "missing_book": False,
        "fillable": fill_ratio >= 0.999,
        "fill_ratio": _round(min(1.0, fill_ratio)) or 0.0,
        "average_price": _round_or_none(average_price),
        "levels_used": levels_used,
        "available_usdc": _round(available_usdc) or 0.0,
    }


def _book_level(level: Any) -> tuple[float | None, float | None]:
    if not isinstance(level, dict):
        return None, None
    return _as_optional_float(level.get("price")), _as_optional_float(level.get("size"))


def _execution_profile(
    signal: dict[str, Any],
    side: str,
    quoted_price: float,
    orderbooks: dict[str, dict[str, Any]],
    planned_stake_usdc: float,
    weather_fee_rate: float,
) -> dict[str, Any]:
    spread = _as_optional_float(signal.get("spread"))
    liquidity = _as_optional_float(signal.get("liquidity"))
    market_volume = _as_optional_float(signal.get("market_volume"))
    flags = []

    spread_penalty = 0.0
    if spread is None:
        spread_penalty = 0.01
        flags.append("missing spread")
    elif spread > 0.02:
        spread_penalty = min(0.02, (spread - 0.02) * 0.50)
        flags.append("wide spread" if spread > 0.04 else "spread penalty")

    liquidity_penalty = 0.0
    if liquidity is None:
        liquidity_penalty = 0.015
        flags.append("missing liquidity")
    elif liquidity < 100:
        liquidity_penalty = 0.02
        flags.append("very thin liquidity")
    elif liquidity < 500:
        liquidity_penalty = 0.01
        flags.append("thin liquidity")
    elif liquidity < 1000:
        liquidity_penalty = 0.005
        flags.append("moderate liquidity")

    volume_penalty = 0.0
    if market_volume is not None and market_volume < 100:
        volume_penalty = 0.005
        flags.append("low market volume")

    price_penalty = 0.0
    if quoted_price <= 0.02 or quoted_price >= 0.95:
        price_penalty = 0.005
        flags.append("extreme price")

    estimated = min(0.05, spread_penalty + liquidity_penalty + volume_penalty + price_penalty)
    book_price = None
    book_levels_used = 0
    book_available_usdc = 0.0
    fill_ratio = 1.0
    fillable = True
    token_id = _token_id_for_side(signal, side)
    if orderbooks:
        depth = _orderbook_depth_estimate(
            book=orderbooks.get(token_id or ""),
            quoted_price=quoted_price,
            planned_stake_usdc=planned_stake_usdc,
            weather_fee_rate=weather_fee_rate,
        )
        book_price = depth["average_price"]
        book_levels_used = depth["levels_used"]
        book_available_usdc = depth["available_usdc"]
        fill_ratio = depth["fill_ratio"]
        fillable = bool(depth["fillable"])
        if depth["missing_book"]:
            estimated = max(estimated, 0.05)
            flags.append("missing orderbook")
        elif not fillable:
            estimated = max(estimated, 0.05)
            flags.append("insufficient orderbook depth")
        elif book_price is not None:
            book_slippage = max(0.0, book_price - quoted_price)
            if book_slippage > 0:
                flags.append("depth slippage")
            estimated = min(0.10, max(estimated, book_slippage))

    if estimated <= 0.005 and (spread or 0.0) <= 0.02 and (liquidity or 0.0) >= 1000:
        quality = "good"
    elif estimated <= 0.02 and (spread or 0.0) <= 0.05 and (liquidity or 0.0) >= 100:
        quality = "fair"
    else:
        quality = "poor"
    return {
        "estimated_slippage": _round(estimated) or 0.0,
        "quality": quality,
        "flags": flags,
        "fillable": fillable,
        "fill_ratio": fill_ratio,
        "book_price": book_price,
        "book_levels_used": book_levels_used,
        "book_available_usdc": book_available_usdc,
        "token_id": token_id or "",
    }


def _maker_profile(
    signal: dict[str, Any],
    side: str,
    taker_price: float,
    taker_expected_roi: float | None,
    fair_probability: float | None,
    scenarios: list[StressScenario],
    prediction_c: float,
    interval_unit: str,
    lower: float | None,
    upper: float | None,
    quote_improvement: float,
    min_fill_score: float,
    adverse_selection_penalty: float,
) -> dict[str, Any]:
    book = _side_book(signal, side=side)
    if fair_probability is None or book["bid"] is None or book["ask"] is None:
        return _empty_maker_profile("missing side book or fair probability")

    tick = 0.001
    spread = max(book["ask"] - book["bid"], 0.0)
    quote_price = min(book["ask"] - tick, book["bid"] + quote_improvement)
    quote_price = min(quote_price, taker_price - tick)
    quote_price = max(0.001, min(0.999, quote_price))
    if quote_price <= 0 or quote_price >= taker_price:
        return _empty_maker_profile("maker quote would not improve taker price")

    fill_score = _maker_fill_score(
        quote_price=quote_price,
        bid=book["bid"],
        ask=book["ask"],
        liquidity=_as_optional_float(signal.get("liquidity")),
        market_volume=_as_optional_float(signal.get("market_volume")),
    )
    edge_if_filled = fair_probability - quote_price - adverse_selection_penalty
    expected_roi_if_filled = (
        fair_probability / quote_price - 1.0 - adverse_selection_penalty / max(quote_price, 0.001)
    )
    stress_edges = [
        _maker_stress_edge(
            side=side,
            prediction_c=prediction_c,
            interval_unit=interval_unit,
            lower=lower,
            upper=upper,
            quote_price=quote_price,
            scenario=scenario,
            adverse_selection_penalty=adverse_selection_penalty,
        )
        for scenario in scenarios
    ]
    worst_edge = min(stress_edges) if stress_edges else None
    eligible = fill_score >= min_fill_score and edge_if_filled > 0 and (worst_edge or -999.0) > 0
    fill_adjusted_roi = expected_roi_if_filled * fill_score
    fallback_taker_roi = fair_probability / max(taker_price, 0.001) - 1.0
    comparison_roi = taker_expected_roi if taker_expected_roi is not None else fallback_taker_roi
    preferred = fill_adjusted_roi > comparison_roi
    if eligible and preferred:
        reason = "maker preferred"
    elif eligible:
        reason = "maker viable, taker expected value is higher"
    else:
        reason = _maker_reject_reason(fill_score, min_fill_score, edge_if_filled, worst_edge)
    return {
        "quote_price": _round(quote_price),
        "price_improvement": _round(taker_price - quote_price),
        "fill_score": _round(fill_score),
        "eligible": eligible,
        "edge_if_filled": _round(edge_if_filled),
        "worst_edge": _round_or_none(worst_edge),
        "expected_roi_if_filled": _round(expected_roi_if_filled),
        "fill_adjusted_expected_roi": _round(fill_adjusted_roi),
        "preferred": preferred,
        "reason": reason,
    }


def _empty_maker_profile(reason: str) -> dict[str, Any]:
    return {
        "quote_price": None,
        "price_improvement": None,
        "fill_score": None,
        "eligible": False,
        "edge_if_filled": None,
        "worst_edge": None,
        "expected_roi_if_filled": None,
        "fill_adjusted_expected_roi": None,
        "preferred": False,
        "reason": reason,
    }


def _side_book(signal: dict[str, Any], side: str) -> dict[str, float | None]:
    yes_bid = _as_optional_float(signal.get("best_bid"))
    yes_ask = _as_optional_float(signal.get("best_ask"))
    if side == "BUY_YES":
        return {"bid": yes_bid, "ask": yes_ask}
    if side == "BUY_NO":
        no_bid = 1.0 - yes_ask if yes_ask is not None else None
        no_ask = 1.0 - yes_bid if yes_bid is not None else None
        return {"bid": no_bid, "ask": no_ask}
    return {"bid": None, "ask": None}


def _maker_fill_score(
    quote_price: float,
    bid: float,
    ask: float,
    liquidity: float | None,
    market_volume: float | None,
) -> float:
    spread = max(ask - bid, 0.001)
    aggression = max(0.0, min(1.0, (quote_price - bid) / spread))
    liquidity_component = _liquidity_score(liquidity)
    volume_component = _volume_score(market_volume)
    spread_component = max(0.0, min(1.0, spread / 0.05))
    return _round(
        0.45 * aggression
        + 0.25 * liquidity_component
        + 0.20 * volume_component
        + 0.10 * spread_component
    ) or 0.0


def _liquidity_score(liquidity: float | None) -> float:
    if liquidity is None:
        return 0.25
    if liquidity >= 1000:
        return 1.0
    if liquidity >= 500:
        return 0.75
    if liquidity >= 100:
        return 0.45
    return 0.15


def _volume_score(market_volume: float | None) -> float:
    if market_volume is None:
        return 0.35
    if market_volume >= 1000:
        return 1.0
    if market_volume >= 250:
        return 0.65
    if market_volume >= 100:
        return 0.35
    return 0.15


def _maker_stress_edge(
    side: str,
    prediction_c: float,
    interval_unit: str,
    lower: float | None,
    upper: float | None,
    quote_price: float,
    scenario: StressScenario,
    adverse_selection_penalty: float,
) -> float:
    shifted_c = prediction_c + scenario.mean_shift_c
    mean = celsius_to_fahrenheit(shifted_c) if interval_unit == "fahrenheit" else shifted_c
    sigma = scenario.sigma_c * 9.0 / 5.0 if interval_unit == "fahrenheit" else scenario.sigma_c
    fair_yes = normal_interval_probability(mean, sigma, lower, upper)
    fair = fair_yes if side == "BUY_YES" else 1.0 - fair_yes
    return fair - quote_price - adverse_selection_penalty


def _maker_reject_reason(
    fill_score: float,
    min_fill_score: float,
    edge_if_filled: float,
    worst_edge: float | None,
) -> str:
    if fill_score < min_fill_score:
        return f"maker fill score below {min_fill_score:.2f}"
    if edge_if_filled <= 0:
        return "maker edge not positive after adverse-selection penalty"
    if worst_edge is not None and worst_edge <= 0:
        return "maker worst stress edge not positive"
    return "maker not preferred"


def _market_context(title: Any, slug: Any) -> dict[str, str]:
    title_text = str(title or "")
    title_match = re.match(r"^(Highest|Lowest) temperature in (.+?) on (.+?)\?", title_text)
    if title_match:
        extreme, city, date = title_match.groups()
        return {"extreme": extreme.lower(), "city": city, "date": date}

    slug_text = str(slug or "")
    slug_match = re.match(r"^(highest|lowest)-temperature-in-(.+)-on-([a-z]+)-(\d+)-(\d{4})", slug_text)
    if slug_match:
        extreme, city, month, day, _year = slug_match.groups()
        return {
            "extreme": extreme,
            "city": city.replace("-", " ").title(),
            "date": f"{month.title()} {day}",
        }
    return {"extreme": "unknown", "city": "unknown", "date": "unknown"}


def _concentration(selected: list[dict[str, Any]], bankroll_usdc: float) -> dict[str, list[dict[str, Any]]]:
    groups = {"city": {}, "date": {}, "extreme": {}}
    for row in selected:
        stake = _as_float(row.get("stake_usdc"), 0.0) or 0.0
        for group_name, key in (
            ("city", row.get("market_city", "unknown")),
            ("date", row.get("market_date", "unknown")),
            ("extreme", row.get("market_extreme", "unknown")),
        ):
            bucket = groups[group_name].setdefault(
                str(key or "unknown"),
                {"name": str(key or "unknown"), "positions": 0, "stake_usdc": 0.0, "bankroll_pct": 0.0},
            )
            bucket["positions"] += 1
            bucket["stake_usdc"] += stake

    result = {}
    for group_name, values in groups.items():
        rows = []
        for row in values.values():
            row["stake_usdc"] = _round(row["stake_usdc"])
            row["bankroll_pct"] = _round(row["stake_usdc"] / bankroll_usdc * 100.0) if bankroll_usdc else 0.0
            rows.append(row)
        result[group_name] = sorted(rows, key=lambda item: (item["stake_usdc"], item["positions"]), reverse=True)
    return result


def _concentration_html(concentration: dict[str, list[dict[str, Any]]]) -> str:
    rows = []
    for group_name in ("city", "date", "extreme"):
        for row in concentration.get(group_name, [])[:10]:
            rows.append(
                "<tr>"
                f"<td>{html.escape(group_name)}</td>"
                f"<td>{html.escape(str(row.get('name', '')))}</td>"
                f"<td>{html.escape(str(row.get('positions', '')))}</td>"
                f"<td>{_money(row.get('stake_usdc'))}</td>"
                f"<td>{html.escape(str(row.get('bankroll_pct', 0.0)))}%</td>"
                "</tr>"
            )
    return "\n".join(rows)


def _max_count(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("positions", 0)) for row in rows), default=0)


def _max_stake(rows: list[dict[str, Any]]) -> float:
    return _round(max((_as_float(row.get("stake_usdc"), 0.0) or 0.0 for row in rows), default=0.0)) or 0.0


def _candidate_stake(row: dict[str, Any], bankroll_usdc: float, max_stake_usdc: float) -> float:
    suggested = _as_float(row.get("suggested_max_stake_usdc"))
    stake = suggested if suggested > 0 else bankroll_usdc * 0.0025
    return _round(min(stake, max_stake_usdc))


def _stress_summary(rows: list[dict[str, Any]]) -> str:
    worst = min(rows, key=lambda row: row["edge"]) if rows else None
    best = max(rows, key=lambda row: row["edge"]) if rows else None
    if not worst or not best:
        return ""
    return f"worst {worst['scenario']} edge={worst['edge']:.4f}; best {best['scenario']} edge={best['edge']:.4f}"


def _quality_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"good": 0, "fair": 0, "poor": 0}
    for row in rows:
        quality = str(row.get("execution_quality") or "poor")
        counts[quality] = counts.get(quality, 0) + 1
    return counts


def _robust_reject_reason(
    rows: list[dict[str, Any]],
    robust_min_edge: float,
    execution: dict[str, Any],
    max_execution_slippage: float,
) -> str:
    if not execution.get("fillable", True):
        return "orderbook depth does not fill planned stake"
    if execution["estimated_slippage"] > max_execution_slippage:
        return f"execution slippage above {max_execution_slippage:.3f}: {execution['estimated_slippage']:.3f}"
    if not rows:
        return "no stress scenarios"
    worst = min(rows, key=lambda row: row["edge"])
    if worst["edge"] < 0:
        return f"negative edge under {worst['scenario']}"
    return f"edge below {robust_min_edge:.3f} under {worst['scenario']}"


def _candidate_html(row: dict[str, Any]) -> str:
    edge_class = "good" if _as_float(row.get("worst_edge"), -999.0) >= 0 else "bad"
    maker_class = "good" if row.get("maker_preferred") == 1 else ""
    exec_pnl = _money(row.get("execution_expected_pnl_usdc"))
    maker_pnl = _money(row.get("maker_fill_adjusted_expected_pnl_usdc"))
    return (
        "<tr>"
        f"<td>{html.escape(str(row.get('side', '')))}</td>"
        f"<td>{html.escape(str(row.get('group_item_title', '')))}</td>"
        f"<td>{_num(row.get('price'))}</td>"
        f"<td class=\"{maker_class}\">{_num(row.get('maker_quote_price'))}</td>"
        f"<td>{_percent(row.get('maker_fill_score'))}</td>"
        f"<td>{_percent(row.get('execution_base_edge'))}</td>"
        f"<td class=\"{maker_class}\">{_percent(row.get('maker_edge_if_filled'))}</td>"
        f"<td class=\"{edge_class}\">{_percent(row.get('worst_edge'))}</td>"
        f"<td>{_money(row.get('stake_usdc'))}</td>"
        f"<td>{exec_pnl} / {maker_pnl}</td>"
        f"<td class=\"title\">{html.escape(str(row.get('event_title', '')))}<div class=\"muted\">{html.escape(str(row.get('execution_quality', '')))} · fill {_percent(row.get('execution_fill_ratio'))} · {html.escape(str(row.get('maker_reason', '')))} · {html.escape(str(row.get('robust_reason', '')))}</div></td>"
        "</tr>"
    )


def _candidate_id(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(key, "")) for key in ("event_slug", "market_slug", "paper_side"))


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


def _metric(label: str, value: Any) -> str:
    return f'<section class="metric"><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></section>'


def _as_float(value: Any, default: float | None = 0.0) -> float | None:
    if value in {"", None}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> float | None:
    return _as_float(value, default=None)


def _round(value: float | int | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else _round(value)


def _money(value: Any) -> str:
    parsed = _as_float(value, default=None)
    return "-" if parsed is None else f"${parsed:,.2f}"


def _num(value: Any) -> str:
    parsed = _as_float(value, default=None)
    if parsed is None:
        return "-"
    return f"{parsed:.4f}".rstrip("0").rstrip(".")


def _percent(value: Any) -> str:
    parsed = _as_float(value, default=None)
    return "-" if parsed is None else f"{parsed * 100:.1f}%"
