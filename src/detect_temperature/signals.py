from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .units import celsius_to_fahrenheit

STRATEGY_VERSION = "betting_v2_conservative"
SIGMA_FLOOR_C = 1.5
SIGMA_MAE_MULTIPLIER = 1.5


def load_station_calibrations(path: str | Path | None) -> dict[str, float]:
    """Load per-station rolling MAE from data/station_calibration.csv.

    Returns {station_id -> rolling_mae_c}. Missing file yields {} and callers
    fall back to the global sigma_c.
    """
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    calibrations: dict[str, float] = {}
    with file_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            station_id = (row.get("station_id") or "").strip().upper()
            mae = _as_float(row.get("rolling_mae_c"))
            if station_id and mae is not None:
                calibrations[station_id] = mae
    return calibrations


def sigma_for_station(
    station_id: str | None,
    calibrations: dict[str, float] | None,
    default_sigma_c: float,
) -> float:
    if not station_id or not calibrations:
        return default_sigma_c
    mae = calibrations.get(station_id.strip().upper())
    if mae is None:
        return default_sigma_c
    return max(SIGMA_FLOOR_C, mae * SIGMA_MAE_MULTIPLIER)


@dataclass(frozen=True)
class TemperatureInterval:
    lower: float | None
    upper: float | None
    unit: str
    label: str


def build_market_signals(
    markets_path: str | Path,
    predictions_path: str | Path,
    output_path: str | Path,
    sigma_c: float = 1.5,
    min_edge: float = 0.03,
    weather_fee_rate: float = 0.05,
    bankroll_usdc: float | None = None,
    min_yes_probability: float = 0.08,
    min_no_probability: float = 0.55,
    max_spread: float = 0.08,
    min_liquidity: float = 0.0,
    guard_no_on_top_bucket: bool = True,
    near_top_no_guard_ratio: float = 0.75,
    allow_buy_yes: bool = True,
    station_calibration_path: str | Path | None = None,
    station_calibrations: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    markets = _read_csv(markets_path)
    predictions = {row.get("slug", ""): row for row in _read_csv(predictions_path)}
    calibrations: dict[str, float] = dict(station_calibrations or {})
    if station_calibration_path:
        calibrations.update(load_station_calibrations(station_calibration_path))
    rows = []
    for market in markets:
        event_slug = market.get("event_slug", "")
        prediction = predictions.get(event_slug)
        row = build_market_signal(
            market=market,
            prediction=prediction,
            sigma_c=sigma_c,
            min_edge=min_edge,
            weather_fee_rate=weather_fee_rate,
            bankroll_usdc=bankroll_usdc,
            min_yes_probability=min_yes_probability,
            min_no_probability=min_no_probability,
            max_spread=max_spread,
            min_liquidity=min_liquidity,
            guard_no_on_top_bucket=guard_no_on_top_bucket,
            near_top_no_guard_ratio=near_top_no_guard_ratio,
            allow_buy_yes=allow_buy_yes,
            station_calibrations=calibrations,
        )
        rows.append(row)
    _apply_event_context(
        rows,
        min_edge=min_edge,
        min_yes_probability=min_yes_probability,
        min_no_probability=min_no_probability,
        max_spread=max_spread,
        min_liquidity=min_liquidity,
        guard_no_on_top_bucket=guard_no_on_top_bucket,
        near_top_no_guard_ratio=near_top_no_guard_ratio,
        bankroll_usdc=bankroll_usdc,
        allow_buy_yes=allow_buy_yes,
    )
    _write_csv(rows, output_path)
    return rows


def build_market_signal(
    market: dict[str, Any],
    prediction: dict[str, Any] | None,
    sigma_c: float = 1.5,
    min_edge: float = 0.03,
    weather_fee_rate: float = 0.05,
    bankroll_usdc: float | None = None,
    min_yes_probability: float = 0.08,
    min_no_probability: float = 0.55,
    max_spread: float = 0.08,
    min_liquidity: float = 0.0,
    guard_no_on_top_bucket: bool = True,
    near_top_no_guard_ratio: float = 0.75,
    allow_buy_yes: bool = True,
    station_calibrations: dict[str, float] | None = None,
) -> dict[str, Any]:
    interval = parse_temperature_interval(market.get("question", ""))
    market_has_ended = _market_has_ended(market.get("end_date"))
    base = dict(market)
    base.update(
        {
            "matched_prediction_slug": prediction.get("slug", "") if prediction else "",
            "prediction_c": prediction.get("corrected_prediction_c", "") if prediction else "",
            "prediction_model": prediction.get("model_name", "") if prediction else "",
            "interval_lower": interval.lower if interval else None,
            "interval_upper": interval.upper if interval else None,
            "interval_unit": interval.unit if interval else "",
            "fair_yes_probability": None,
            "fair_no_probability": None,
            "yes_buy_price": None,
            "no_buy_price": None,
            "yes_fee_per_share": None,
            "no_fee_per_share": None,
            "yes_net_edge": None,
            "no_net_edge": None,
            "strategy_version": STRATEGY_VERSION,
            "model_sigma_c": sigma_c,
            "visible_bucket_count": None,
            "visible_bucket_rank": None,
            "visible_top_bucket": "",
            "visible_top_bucket_probability": None,
            "is_visible_top_bucket": None,
            "is_near_visible_top_bucket": None,
            "decision_reason": "",
            "risk_flags": "",
            "paper_side": "NO_DATA",
            "paper_price": None,
            "paper_fair_probability": None,
            "paper_net_edge": None,
            "paper_decision_score": None,
            "suggested_max_stake_usdc": None,
            "market_has_ended": int(market_has_ended),
            "reason": "",
        }
    )
    if prediction is None:
        base["reason"] = "no matching prediction for event_slug"
        return base
    if interval is None:
        base["reason"] = "could not parse temperature interval"
        return base

    station_verified = prediction.get("station_verified")
    if station_verified not in {None, "", "1", 1, True, "True", "true"}:
        base["station_verified"] = 0
        base["station_verification_reason"] = str(
            prediction.get("station_verification_reason") or "station not verified"
        )
        _set_no_trade(base, f"station not verified: {base['station_verification_reason']}")
        return base
    base["station_verified"] = 1 if station_verified in {"1", 1, True, "True", "true"} else ""
    base["station_verification_reason"] = str(prediction.get("station_verification_reason") or "")

    mean_c = _as_float(prediction.get("corrected_prediction_c"))
    if mean_c is None:
        base["reason"] = "missing corrected_prediction_c"
        return base

    station_id = str(prediction.get("station_id") or "").strip()
    effective_sigma_c = sigma_for_station(station_id, station_calibrations, sigma_c)
    base["model_sigma_c"] = effective_sigma_c
    base["model_sigma_source"] = (
        "station_calibration"
        if station_calibrations and station_id.upper() in (station_calibrations or {})
        else "default"
    )

    mean = celsius_to_fahrenheit(mean_c) if interval.unit == "fahrenheit" else mean_c
    sigma = (
        effective_sigma_c * 9.0 / 5.0 if interval.unit == "fahrenheit" else effective_sigma_c
    )
    fair_yes = normal_interval_probability(mean, sigma, interval.lower, interval.upper)
    fair_no = 1.0 - fair_yes

    yes_buy_price = _first_float(market.get("best_ask"), market.get("yes_price"))
    yes_bid = _as_float(market.get("best_bid"))
    no_buy_price = (1.0 - yes_bid) if yes_bid is not None else _first_float(market.get("no_price"))
    yes_fee = fee_per_share(yes_buy_price, weather_fee_rate) if yes_buy_price is not None else None
    no_fee = fee_per_share(no_buy_price, weather_fee_rate) if no_buy_price is not None else None
    yes_edge = fair_yes - yes_buy_price - (yes_fee or 0.0) if yes_buy_price is not None else None
    no_edge = fair_no - no_buy_price - (no_fee or 0.0) if no_buy_price is not None else None

    # Kelly fraction is annotation only - it does NOT drive sizing today.
    # Strategy lab still picks a flat stake (suggested_max_stake_usdc) per
    # bankroll_100 risk profile. We expose Kelly so the chapter-4 diagnostic
    # script (and human eyes) can see what the math thinks the stake should
    # be for each row, without changing live behavior.
    yes_kelly = kelly_fraction(yes_edge, yes_buy_price)
    no_kelly = kelly_fraction(no_edge, no_buy_price)

    base.update(
        {
            "fair_yes_probability": round(fair_yes, 6),
            "fair_no_probability": round(fair_no, 6),
            "yes_buy_price": _round_or_none(yes_buy_price),
            "no_buy_price": _round_or_none(no_buy_price),
            "yes_fee_per_share": _round_or_none(yes_fee),
            "no_fee_per_share": _round_or_none(no_fee),
            "yes_net_edge": _round_or_none(yes_edge),
            "no_net_edge": _round_or_none(no_edge),
            "yes_kelly_fraction": _round_or_none(yes_kelly),
            "no_kelly_fraction": _round_or_none(no_kelly),
        }
    )

    _apply_betting_decision(
        base,
        min_edge=min_edge,
        min_yes_probability=min_yes_probability,
        min_no_probability=min_no_probability,
        max_spread=max_spread,
        min_liquidity=min_liquidity,
        guard_no_on_top_bucket=guard_no_on_top_bucket,
        near_top_no_guard_ratio=near_top_no_guard_ratio,
        bankroll_usdc=bankroll_usdc,
        allow_buy_yes=allow_buy_yes,
    )
    return base


def parse_temperature_interval(question: str) -> TemperatureInterval | None:
    normalized = _normalize_question(question)

    range_match = re.search(
        r"\b(?:between\s+)?(-?\d+(?:\.\d+)?)\s*(?:-|to|–|—)\s*(-?\d+(?:\.\d+)?)\s*(?:degrees\s*)?([cf])\b",
        normalized,
        re.I,
    )
    if range_match:
        lower, upper, unit = range_match.groups()
        lower_f = float(lower)
        upper_f = float(upper)
        return TemperatureInterval(
            lower=lower_f,
            upper=upper_f + _bin_step(unit),
            unit=_unit_name(unit),
            label=range_match.group(0),
        )

    higher_match = re.search(
        r"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:degrees\s*)?([cf])\s+or\s+higher\b",
        normalized,
        re.I,
    )
    if higher_match:
        value, unit = higher_match.groups()
        value_f = float(value)
        return TemperatureInterval(
            lower=value_f - _single_degree_half_width(unit),
            upper=None,
            unit=_unit_name(unit),
            label=higher_match.group(0),
        )

    below_match = re.search(
        r"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:degrees\s*)?([cf])\s+or\s+(?:lower|below)\b",
        normalized,
        re.I,
    )
    if below_match:
        value, unit = below_match.groups()
        value_f = float(value)
        return TemperatureInterval(
            lower=None,
            upper=value_f + _single_degree_half_width(unit),
            unit=_unit_name(unit),
            label=below_match.group(0),
        )

    exact_match = re.search(r"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:degrees\s*)?([cf])\b", normalized, re.I)
    if exact_match:
        value, unit = exact_match.groups()
        value_f = float(value)
        half_width = _single_degree_half_width(unit)
        return TemperatureInterval(
            lower=value_f - half_width,
            upper=value_f + half_width,
            unit=_unit_name(unit),
            label=exact_match.group(0),
        )
    return None


def normal_interval_probability(
    mean: float,
    sigma: float,
    lower: float | None,
    upper: float | None,
) -> float:
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    lower_prob = 0.0 if lower is None else _normal_cdf((lower - mean) / sigma)
    upper_prob = 1.0 if upper is None else _normal_cdf((upper - mean) / sigma)
    return max(0.0, min(1.0, upper_prob - lower_prob))


def fee_per_share(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1.0 - price)


def kelly_fraction(edge: float | None, price: float | None) -> float | None:
    """Full Kelly fraction for a Polymarket-style binary contract.

    f* = (q - p) / (1 - p), where the edge passed in is already (q - p)
    after fees if available. We do NOT clamp to [0, 1] here - a negative
    return value means "no edge, do not bet" and the caller decides; a
    value above some sane cap should also be handled by the caller, not
    silently smoothed here.

    Returns None if inputs are missing or the price is at the boundary.
    """
    if edge is None or price is None:
        return None
    denom = 1.0 - price
    if denom <= 0:
        return None
    return edge / denom


def _apply_event_context(
    rows: list[dict[str, Any]],
    min_edge: float,
    min_yes_probability: float,
    min_no_probability: float,
    max_spread: float,
    min_liquidity: float,
    guard_no_on_top_bucket: bool,
    near_top_no_guard_ratio: float,
    bankroll_usdc: float | None,
    allow_buy_yes: bool = True,
) -> None:
    rows_by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_event.setdefault(str(row.get("event_slug", "")), []).append(row)

    for event_rows in rows_by_event.values():
        priced_rows = [
            row for row in event_rows
            if _as_float(row.get("fair_yes_probability")) is not None
        ]
        priced_rows.sort(key=lambda row: _as_float(row.get("fair_yes_probability")) or 0.0, reverse=True)
        top_row = priced_rows[0] if priced_rows else None
        top_probability = _as_float(top_row.get("fair_yes_probability")) if top_row else None
        top_bucket = str(top_row.get("group_item_title", "")) if top_row else ""
        ranks = {id(row): index + 1 for index, row in enumerate(priced_rows)}

        for row in event_rows:
            row["visible_bucket_count"] = len(priced_rows) if priced_rows else None
            row["visible_bucket_rank"] = ranks.get(id(row))
            row["visible_top_bucket"] = top_bucket
            row["visible_top_bucket_probability"] = _round_or_none(top_probability)
            row["is_visible_top_bucket"] = int(ranks.get(id(row)) == 1) if ranks.get(id(row)) else None
            fair_yes = _as_float(row.get("fair_yes_probability"))
            if fair_yes is None:
                row["decision_reason"] = row.get("reason", "")
                continue
            is_near_top = (
                fair_yes is not None
                and top_probability is not None
                and top_probability > 0
                and fair_yes >= top_probability * near_top_no_guard_ratio
            )
            row["is_near_visible_top_bucket"] = int(is_near_top) if fair_yes is not None else None
            _apply_betting_decision(
                row,
                min_edge=min_edge,
                min_yes_probability=min_yes_probability,
                min_no_probability=min_no_probability,
                max_spread=max_spread,
                min_liquidity=min_liquidity,
                guard_no_on_top_bucket=guard_no_on_top_bucket,
                near_top_no_guard_ratio=near_top_no_guard_ratio,
                bankroll_usdc=bankroll_usdc,
                allow_buy_yes=allow_buy_yes,
            )


def _apply_betting_decision(
    row: dict[str, Any],
    min_edge: float,
    min_yes_probability: float,
    min_no_probability: float,
    max_spread: float,
    min_liquidity: float,
    guard_no_on_top_bucket: bool,
    near_top_no_guard_ratio: float,
    bankroll_usdc: float | None,
    allow_buy_yes: bool = True,
) -> None:
    health_block = _market_health_block(row, max_spread=max_spread, min_liquidity=min_liquidity)
    if health_block:
        _set_no_trade(row, health_block)
        return

    fair_yes = _as_float(row.get("fair_yes_probability"))
    fair_no = _as_float(row.get("fair_no_probability"))
    yes_edge = _as_float(row.get("yes_net_edge"))
    no_edge = _as_float(row.get("no_net_edge"))
    yes_buy_price = _as_float(row.get("yes_buy_price"))
    no_buy_price = _as_float(row.get("no_buy_price"))
    if fair_yes is None or fair_no is None:
        _set_no_trade(row, "missing probability")
        return

    candidates = []
    rejected = []
    if not allow_buy_yes:
        rejected.append("BUY_YES disabled by risk profile")
    elif yes_edge is None or yes_buy_price is None:
        rejected.append("BUY_YES no executable price")
    elif yes_edge < min_edge:
        rejected.append("BUY_YES edge below threshold")
    elif fair_yes < min_yes_probability:
        rejected.append("BUY_YES probability too low")
    else:
        candidates.append(("BUY_YES", yes_buy_price, fair_yes, yes_edge, yes_edge))

    no_is_guarded = _no_side_is_guarded(
        row,
        fair_yes=fair_yes,
        guard_no_on_top_bucket=guard_no_on_top_bucket,
        near_top_no_guard_ratio=near_top_no_guard_ratio,
    )
    if no_edge is None or no_buy_price is None:
        rejected.append("BUY_NO no executable price")
    elif no_edge < min_edge:
        rejected.append("BUY_NO edge below threshold")
    elif fair_no < min_no_probability:
        rejected.append("BUY_NO probability too low")
    elif no_is_guarded:
        rejected.append(no_is_guarded)
    else:
        candidates.append(("BUY_NO", no_buy_price, fair_no, no_edge, no_edge))

    if not candidates:
        reason = "; ".join(rejected) if rejected else "no valid candidate"
        _set_no_trade(row, reason)
        return

    side, price, fair_probability, edge, score = max(candidates, key=lambda item: item[4])
    row["paper_side"] = side
    row["paper_price"] = _round_or_none(price)
    row["paper_fair_probability"] = _round_or_none(fair_probability)
    row["paper_net_edge"] = _round_or_none(edge)
    row["paper_decision_score"] = _round_or_none(score)
    row["suggested_max_stake_usdc"] = _suggest_stake(bankroll_usdc, edge, price)
    row["decision_reason"] = _decision_reason(row, side=side)
    row["risk_flags"] = _risk_flags(row)
    row["reason"] = row["decision_reason"]


def _set_no_trade(row: dict[str, Any], reason: str) -> None:
    row["paper_side"] = "NO_TRADE"
    row["paper_price"] = None
    row["paper_fair_probability"] = None
    row["paper_net_edge"] = None
    row["paper_decision_score"] = None
    row["suggested_max_stake_usdc"] = None
    row["decision_reason"] = reason
    row["risk_flags"] = _risk_flags(row)
    row["reason"] = reason


def _market_health_block(row: dict[str, Any], max_spread: float, min_liquidity: float) -> str | None:
    if _as_float(row.get("market_has_ended")) == 1:
        return "market already ended"
    if _truthy(row.get("closed")):
        return "market closed"
    if not _truthy(row.get("active"), default=True):
        return "market inactive"
    if not _truthy(row.get("accepting_orders"), default=True):
        return "market not accepting orders"
    spread = _as_float(row.get("spread"))
    if spread is not None and spread > max_spread:
        return "spread too wide"
    liquidity = _as_float(row.get("liquidity"))
    if liquidity is not None and liquidity < min_liquidity:
        return "liquidity below minimum"
    return None


def _no_side_is_guarded(
    row: dict[str, Any],
    fair_yes: float,
    guard_no_on_top_bucket: bool,
    near_top_no_guard_ratio: float,
) -> str | None:
    if guard_no_on_top_bucket and _as_float(row.get("is_visible_top_bucket")) == 1:
        return "BUY_NO blocked: bucket is model top bucket"
    top_probability = _as_float(row.get("visible_top_bucket_probability"))
    if (
        guard_no_on_top_bucket
        and top_probability is not None
        and top_probability > 0
        and fair_yes >= top_probability * near_top_no_guard_ratio
    ):
        return "BUY_NO blocked: bucket is near model top bucket"
    return None


def _decision_reason(row: dict[str, Any], side: str) -> str:
    if side == "BUY_YES":
        return (
            "BUY_YES: our bucket probability is above market ask after fee; "
            f"rank {row.get('visible_bucket_rank') or '-'} of {row.get('visible_bucket_count') or '-'}"
        )
    if side == "BUY_NO":
        return (
            "BUY_NO: model thinks this bucket is unlikely and it is not the model top bucket; "
            f"top bucket is {row.get('visible_top_bucket') or '-'}"
        )
    return ""


def _risk_flags(row: dict[str, Any]) -> str:
    flags = []
    if _as_float(row.get("visible_bucket_count")) is not None and (_as_float(row.get("visible_bucket_count")) or 0) < 3:
        flags.append("few visible buckets")
    if _as_float(row.get("is_visible_top_bucket")) == 1:
        flags.append("model top bucket")
    spread = _as_float(row.get("spread"))
    if spread is not None and spread > 0.04:
        flags.append("wide spread")
    liquidity = _as_float(row.get("liquidity"))
    if liquidity is not None and liquidity < 100:
        flags.append("thin liquidity")
    if _as_float(row.get("market_has_ended")) == 1:
        flags.append("ended")
    return "; ".join(flags)


def _suggest_stake(bankroll_usdc: float | None, edge: float | None, price: float | None) -> float | None:
    if bankroll_usdc is None or edge is None or price is None or edge <= 0 or price <= 0:
        return None
    # Conservative paper sizing: cap at 0.5% bankroll and scale down near thin edges.
    risk_fraction = min(0.005, max(0.001, edge / 20.0))
    return round(bankroll_usdc * risk_fraction, 2)


def _market_has_ended(end_date: Any) -> bool:
    if not end_date:
        return False
    try:
        value = str(end_date).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normalize_question(question: str) -> str:
    return (
        question.lower()
        .replace("°", "")
        .replace("º", "")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )


def _unit_name(unit: str) -> str:
    return "fahrenheit" if unit.lower() == "f" else "celsius"


def _bin_step(unit: str) -> float:
    # A displayed 58-59F bin covers integer values 58 and 59; exact Celsius bins are usually 1C.
    return 1.0


def _single_degree_half_width(unit: str) -> float:
    return 0.5


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


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def _as_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any, default: bool = False) -> bool:
    if value in {"", None}:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)
