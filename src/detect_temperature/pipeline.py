from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .evaluation import regression_metrics, time_ordered_split
from .features import build_feature_row
from .markets import (
    load_raw_markets,
    normalize_markets,
    normalize_polymarket_events,
    read_targets_csv,
    write_targets_csv,
    write_targets_jsonl,
)
from .models.baseline import ExtremeTemperatureBaseline
from .models.gbm import BiasCorrectedGBM, select_available_feature_columns
from .near_close import (
    NearCloseInput,
    fetch_intraday_max_min,
    hours_remaining_until,
    refined_bucket_probability,
)
from .paper import open_paper_portfolio, open_strategy_paper_portfolio, settle_paper_portfolio
from .polymarket import (
    PolymarketClobClient,
    PolymarketWeatherClient,
    flatten_temperature_markets,
    token_ids_from_market_records,
    write_json,
    write_polymarket_markets_csv,
)
from .resolved_eval import evaluate_resolved_predictions
from .schema import MarketTarget
from .signals import build_market_signals as build_market_signals_file
from .signals import fee_per_share, load_station_calibrations, sigma_for_station
from .sources.actuals import collect_actual_for_target, error_actual_for_target
from .sources.base import ForecastProvider, ObservationProvider, StationCatalog, StationMetadata
from .sources.aviation_weather import AviationWeatherStationCatalog
from .sources.manual import ManualStationCatalog
from .strategy_lab import run_strategy_lab as run_strategy_lab_file
from .units import celsius_to_fahrenheit


def build_targets(
    input_path: str | Path,
    csv_path: str | Path | None = None,
    jsonl_path: str | Path | None = None,
    include_unknown: bool = False,
) -> list[MarketTarget]:
    targets = normalize_markets(load_raw_markets(input_path), include_unknown=include_unknown)
    if csv_path:
        write_targets_csv(targets, csv_path)
    if jsonl_path:
        write_targets_jsonl(targets, jsonl_path)
    return targets


def build_polymarket_targets(
    events_path: str | Path,
    csv_path: str | Path | None = None,
    jsonl_path: str | Path | None = None,
    reference_targets_path: str | Path | None = None,
    include_unknown: bool = False,
) -> list[MarketTarget]:
    reference_targets = []
    if reference_targets_path and Path(reference_targets_path).exists():
        reference_targets = read_targets_csv(reference_targets_path)
    targets = normalize_polymarket_events(
        load_raw_markets(events_path),
        reference_targets=reference_targets,
        include_unknown=include_unknown,
    )
    if csv_path:
        write_targets_csv(targets, csv_path)
    if jsonl_path:
        write_targets_jsonl(targets, jsonl_path)
    return targets


def build_features(
    targets_path: str | Path,
    output_path: str | Path,
    station_catalog: StationCatalog | None = None,
    forecast_provider: ForecastProvider | None = None,
    observation_provider: ObservationProvider | None = None,
) -> list[dict]:
    targets = read_targets_csv(targets_path)
    rows = []
    as_of = datetime.now(timezone.utc)
    station_cache = {}
    forecast_cache = {}
    observation_cache = {}
    for target in targets:
        station = None
        if station_catalog:
            if target.station_id not in station_cache:
                station_cache[target.station_id] = station_catalog.lookup(target.station_id)
            station = station_cache[target.station_id]

        forecast = None
        if forecast_provider and station and target.target_date:
            forecast_key = (target.station_id, target.target_date)
            try:
                if forecast_key not in forecast_cache:
                    forecast_cache[forecast_key] = forecast_provider.forecast_daily(station, target.target_date)
                forecast = forecast_cache[forecast_key]
            except Exception as exc:
                forecast = None
                print(f"forecast skipped for {target.slug}: {exc}")

        observation = None
        if observation_provider and target.station_id:
            try:
                if target.station_id not in observation_cache:
                    observation_cache[target.station_id] = observation_provider.latest(target.station_id)
                observation = observation_cache[target.station_id]
            except Exception as exc:
                observation = None
                print(f"observation skipped for {target.slug}: {exc}")
        rows.append(build_feature_row(target, station, forecast, observation, as_of=as_of))

    write_records_csv(rows, output_path)
    return rows


def predict_baseline(features_path: str | Path, output_path: str | Path) -> list[dict]:
    rows = read_records_csv(features_path)
    model = ExtremeTemperatureBaseline()
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    predictions = []
    for row in rows:
        prediction = model.predict_one(row)
        enriched = dict(row)
        enriched["prediction_c"] = prediction
        enriched["prediction_f"] = celsius_to_fahrenheit(prediction) if prediction is not None else None
        enriched["prediction_resolution_unit"] = row.get("target_unit", "")
        enriched["prediction_resolution_value"] = _convert_to_target_unit(prediction, row.get("target_unit", ""))
        enriched["model_name"] = model.model_name
        enriched["created_at"] = created_at
        predictions.append(enriched)
    write_records_csv(predictions, output_path)
    return predictions


def collect_actuals(
    targets_path: str | Path,
    output_path: str | Path,
    station_catalog: StationCatalog | None = None,
    finalization_lag_days: int = 1,
    portfolio_path: str | Path | None = None,
    paper_runs_root: str | Path | None = None,
) -> list[dict]:
    """Collect resolved temperatures for every target we currently know.

    Merges into any pre-existing `output_path` rather than overwriting:
      - ok rows in the existing file survive unless the target is re-fetched
        and returns fresh ok data.
      - pending/error rows get replaced when we successfully fetch.
      - rows for slugs that are no longer in targets_path are preserved as-is
        (so rotating targets.csv daily doesn't wipe historical actuals).

    Stuck-open recovery (added 2026-05-16):
      Paper positions that are still 'open' / 'at_risk' / 'pending_actual'
      after their target_date passed must keep getting their actuals fetched,
      even though their slug rotated out of `targets_path` days ago. We
      reconstruct minimal MarketTarget objects for those slugs by parsing
      the slug for date + extreme, taking the unit from the portfolio row,
      and looking up the station_id in archived targets.csv files under
      `paper_runs_root`. Defaults locate paper_portfolio.csv under
      artifacts/ and the archive root under artifacts/paper_runs/.
    """
    output_path = Path(output_path)
    existing: dict[str, dict] = {}
    if output_path.exists():
        try:
            existing = {row.get("slug", ""): row for row in read_records_csv(output_path)}
        except Exception:
            existing = {}

    targets = list(read_targets_csv(targets_path))
    known_slugs = {target.slug for target in targets}
    stuck = _stuck_paper_targets(
        portfolio_path=portfolio_path,
        archive_root=paper_runs_root,
        already_queued=known_slugs,
        targets_path=Path(targets_path),
    )
    if stuck:
        targets.extend(stuck)

    station_cache: dict[str, Any] = {}
    fresh_records: list[dict] = []
    for target in targets:
        station = None
        if station_catalog:
            if target.station_id not in station_cache:
                station_cache[target.station_id] = station_catalog.lookup(target.station_id)
            station = station_cache[target.station_id]
        try:
            actual = collect_actual_for_target(
                target=target,
                station=station,
                finalization_lag_days=finalization_lag_days,
            )
        except Exception as exc:
            fresh_records.append(error_actual_for_target(target, str(exc)).to_record())
            continue
        fresh_records.append(actual.to_record())

    # Merge: fresh rows win only when the existing row is not already ok, or
    # when the fresh row itself is ok. This protects accidentally wiping a
    # resolved temperature if a later refresh returns "pending" for the same
    # slug (e.g. upstream API briefly unavailable).
    merged_by_slug: dict[str, dict] = dict(existing)
    for row in fresh_records:
        slug = row.get("slug", "")
        if not slug:
            continue
        old = merged_by_slug.get(slug)
        if old is None:
            merged_by_slug[slug] = row
            continue
        fresh_ok = row.get("status") == "ok"
        old_ok = old.get("status") == "ok"
        if fresh_ok or not old_ok:
            merged_by_slug[slug] = row

    merged_records = list(merged_by_slug.values())
    write_records_csv(merged_records, output_path)
    return merged_records


_STUCK_OPEN_STATUSES = frozenset({"open", "pending_actual", "at_risk"})

_MONTHS_LOWER = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _stuck_paper_targets(
    portfolio_path: str | Path | None,
    archive_root: str | Path | None,
    already_queued: set[str],
    targets_path: Path,
) -> list[MarketTarget]:
    """Build MarketTarget objects for paper positions still 'open' whose
    slug is no longer in the current targets.csv."""
    import re
    from datetime import date

    project_root = targets_path.parent.parent if targets_path.parent.name == "data" else None

    if portfolio_path is None and project_root is not None:
        portfolio_path = project_root / "artifacts" / "paper_portfolio.csv"
    if archive_root is None and project_root is not None:
        archive_root = project_root / "artifacts" / "paper_runs"

    portfolio_path = Path(portfolio_path) if portfolio_path else None
    archive_root = Path(archive_root) if archive_root else None
    if portfolio_path is None or not portfolio_path.exists():
        return []

    # Build slug -> station_id lookup from every archived targets.csv.
    # Current rotation already wins via `already_queued` short-circuit.
    archived_station: dict[str, str] = {}
    archived_unit: dict[str, str] = {}
    if archive_root and archive_root.exists():
        for archived in sorted(archive_root.glob("*/targets.csv")):
            try:
                for row in read_records_csv(archived):
                    slug = row.get("slug", "")
                    if not slug:
                        continue
                    if slug not in archived_station and row.get("station_id"):
                        archived_station[slug] = row["station_id"]
                    if slug not in archived_unit and row.get("target_unit"):
                        archived_unit[slug] = row["target_unit"]
            except Exception:
                continue

    recovered: list[MarketTarget] = []
    seen: set[str] = set()
    try:
        portfolio = read_records_csv(portfolio_path)
    except Exception:
        return []

    for row in portfolio:
        status = (row.get("status") or "").strip().lower()
        if status not in _STUCK_OPEN_STATUSES:
            continue
        slug = (row.get("event_slug") or "").strip()
        if not slug or slug in already_queued or slug in seen:
            continue

        slug_match = re.search(r"on-([a-z]+)-(\d{1,2})-(\d{4})", slug)
        if not slug_match:
            continue
        month_name, day, year = slug_match.groups()
        month = _MONTHS_LOWER.get(month_name.lower())
        if month is None:
            continue
        try:
            target_date = date(int(year), month, int(day))
        except ValueError:
            continue

        if "highest-temperature" in slug:
            extreme = "max"
        elif "lowest-temperature" in slug:
            extreme = "min"
        else:
            continue

        station_id = archived_station.get(slug, "") or (row.get("station_id") or "").strip()
        if not station_id:
            continue
        unit = (
            archived_unit.get(slug)
            or (row.get("interval_unit") or "").strip()
            or "celsius"
        )
        if station_id == "HKO":
            domain = "weather.gov.hk"
            url = "https://www.weather.gov.hk/"
            description = "Hong Kong Observatory"
        else:
            domain = "wunderground.com"
            url = f"https://www.wunderground.com/weather/{station_id}"
            description = ""

        recovered.append(MarketTarget(
            title=row.get("event_title") or slug,
            slug=slug,
            city="",
            location_name="",
            target_date=target_date,
            target_extreme=extreme,
            target_unit=unit,
            station_id=station_id,
            resolution_source_url=url,
            source_domain=domain,
            description=description,
        ))
        seen.add(slug)

    return recovered


def train_gbm_model(
    training_path: str | Path,
    model_path: str | Path,
    metrics_path: str | Path | None = None,
    holdout_predictions_path: str | Path | None = None,
    report_path: str | Path | None = None,
    test_fraction: float = 0.33,
) -> dict:
    frame = pd.read_csv(training_path)
    frame = frame.dropna(subset=["observed_temp_c"]).copy()
    if frame.empty:
        raise ValueError("No labeled rows in training data")

    train, test, split = time_ordered_split(frame, test_fraction=test_fraction)
    feature_columns = select_available_feature_columns(train)

    evaluation_model = BiasCorrectedGBM(feature_columns=feature_columns)
    evaluation_model.fit(train)

    holdout = test.copy()
    holdout["baseline_prediction_c"] = _baseline_predictions(holdout)
    holdout["corrected_prediction_c"] = evaluation_model.predict(holdout)

    metric_records = []
    for metric in regression_metrics(
        holdout,
        actual_column="observed_temp_c",
        prediction_column="baseline_prediction_c",
        model_name=ExtremeTemperatureBaseline.model_name,
        split="holdout",
    ):
        metric_records.append(metric.to_record())
    for metric in regression_metrics(
        holdout,
        actual_column="observed_temp_c",
        prediction_column="corrected_prediction_c",
        model_name=BiasCorrectedGBM.model_name,
        split="holdout",
    ):
        metric_records.append(metric.to_record())

    final_model = BiasCorrectedGBM(feature_columns=feature_columns)
    final_model.fit(frame)
    final_model.save(model_path)

    summary = {
        "model_name": BiasCorrectedGBM.model_name,
        "model_path": str(model_path),
        "training_path": str(training_path),
        "rows": int(frame.shape[0]),
        "train_rows": int(train.shape[0]),
        "holdout_rows": int(holdout.shape[0]),
        "feature_columns": feature_columns,
        "split": split,
        "metrics": metric_records,
    }

    if metrics_path:
        _write_json(summary, metrics_path)
    if holdout_predictions_path:
        write_records_csv(_records_from_frame(holdout), holdout_predictions_path)
    if report_path:
        _write_model_report(summary, report_path)
    return summary


def predict_gbm(
    features_path: str | Path,
    model_path: str | Path,
    output_path: str | Path,
    station_calibration_path: str | Path | None = "data/station_calibration.csv",
) -> list[dict]:
    frame = pd.read_csv(features_path)
    model = BiasCorrectedGBM.load(model_path)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    frame["baseline_prediction_c"] = _baseline_predictions(frame)
    frame["gbm_prediction_c"] = model.predict(frame)

    bias_map = _load_bias_map(station_calibration_path)
    if bias_map and "station_id" in frame.columns:
        bias_series = frame["station_id"].map(bias_map).fillna(0.0)
        frame["station_bias_c"] = bias_series
        frame["corrected_prediction_c"] = frame["gbm_prediction_c"] - bias_series
        frame["bias_correction_applied"] = (bias_series != 0).astype(int)
    else:
        frame["station_bias_c"] = 0.0
        frame["corrected_prediction_c"] = frame["gbm_prediction_c"]
        frame["bias_correction_applied"] = 0

    frame["corrected_prediction_f"] = frame["corrected_prediction_c"].map(
        lambda value: celsius_to_fahrenheit(value) if pd.notna(value) else None
    )
    units = frame["target_unit"].fillna("") if "target_unit" in frame.columns else pd.Series([""] * len(frame))
    frame["corrected_prediction_resolution_unit"] = units
    frame["corrected_prediction_resolution_value"] = [
        _convert_to_target_unit(value, unit)
        for value, unit in zip(frame["corrected_prediction_c"], units, strict=False)
    ]
    frame["model_name"] = model.model_name
    frame["created_at"] = created_at

    records = _records_from_frame(frame)
    write_records_csv(records, output_path)
    return records


def _load_bias_map(path: str | Path | None) -> dict[str, float]:
    """Return {station_id -> rolling_bias_c} from calibration CSV or {}."""
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    with file_path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        out: dict[str, float] = {}
        for row in reader:
            station_id = (row.get("station_id") or "").strip().upper()
            try:
                bias = float(row.get("rolling_bias_c") or "")
            except ValueError:
                continue
            if station_id:
                out[station_id] = bias
        return out


def scan_polymarket_weather(
    output_path: str | Path,
    raw_output_path: str | Path | None = None,
    geoblock_output_path: str | Path | None = None,
) -> list[dict]:
    client = PolymarketWeatherClient()
    events = client.fetch_weather_events()
    markets = flatten_temperature_markets(events)
    write_polymarket_markets_csv(markets, output_path)
    if raw_output_path:
        write_json(events, raw_output_path)
    if geoblock_output_path:
        write_json(client.fetch_geoblock(), geoblock_output_path)
    return [market.to_record() for market in markets]


def fetch_clob_orderbooks(
    markets_path: str | Path,
    output_path: str | Path,
    include_no: bool = True,
    limit: int | None = None,
    insecure: bool = False,
) -> dict:
    market_records = read_records_csv(markets_path)
    token_ids = token_ids_from_market_records(market_records, include_no=include_no)
    if limit is not None:
        token_ids = token_ids[:limit]
    client = PolymarketClobClient(verify_tls=not insecure)
    books = client.fetch_order_books(token_ids)
    payload = {
        "source_markets_path": str(markets_path),
        "requested_token_ids": len(token_ids),
        "books": books,
    }
    write_json(payload, output_path)
    return payload


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
    station_calibration_path: str | Path | None = "data/station_calibration.csv",
) -> list[dict]:
    return build_market_signals_file(
        markets_path=markets_path,
        predictions_path=predictions_path,
        output_path=output_path,
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
        station_calibration_path=station_calibration_path,
    )


def open_paper_trades(
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
) -> dict:
    return open_paper_portfolio(
        signals_path=signals_path,
        output_path=output_path,
        state_path=state_path,
        dashboard_path=dashboard_path,
        bankroll_usdc=bankroll_usdc,
        min_edge=min_edge,
        max_positions=max_positions,
        max_stake_usdc=max_stake_usdc,
        max_total_exposure_pct=max_total_exposure_pct,
        max_event_exposure_pct=max_event_exposure_pct,
        min_price=min_price,
        max_price=max_price,
        allow_ended=allow_ended,
    )


def open_strategy_paper_trades(
    strategy_portfolio_path: str | Path,
    output_path: str | Path,
    state_path: str | Path | None = None,
    dashboard_path: str | Path | None = None,
    bankroll_usdc: float = 1000.0,
    max_positions: int = 100,
    execution_mode: str = "taker",
    weather_fee_rate: float = 0.05,
    maker_fee_rate: float = 0.0,
) -> dict:
    return open_strategy_paper_portfolio(
        strategy_portfolio_path=strategy_portfolio_path,
        output_path=output_path,
        state_path=state_path,
        dashboard_path=dashboard_path,
        bankroll_usdc=bankroll_usdc,
        max_positions=max_positions,
        execution_mode=execution_mode,
        weather_fee_rate=weather_fee_rate,
        maker_fee_rate=maker_fee_rate,
    )


def settle_paper_trades(
    portfolio_path: str | Path,
    actuals_path: str | Path,
    output_path: str | Path,
    state_path: str | Path | None = None,
    dashboard_path: str | Path | None = None,
    bankroll_usdc: float = 1000.0,
) -> dict:
    return settle_paper_portfolio(
        portfolio_path=portfolio_path,
        actuals_path=actuals_path,
        output_path=output_path,
        state_path=state_path,
        dashboard_path=dashboard_path,
        bankroll_usdc=bankroll_usdc,
    )


def evaluate_resolved_model(
    predictions_path: str | Path,
    actuals_path: str | Path,
    signals_path: str | Path,
    event_output_path: str | Path,
    signal_output_path: str | Path,
    report_path: str | Path | None = None,
) -> dict:
    return evaluate_resolved_predictions(
        predictions_path=predictions_path,
        actuals_path=actuals_path,
        signals_path=signals_path,
        event_output_path=event_output_path,
        signal_output_path=signal_output_path,
        report_path=report_path,
    )


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
) -> dict:
    return run_strategy_lab_file(
        signals_path=signals_path,
        candidates_output_path=candidates_output_path,
        portfolio_output_path=portfolio_output_path,
        summary_output_path=summary_output_path,
        report_path=report_path,
        orderbooks_path=orderbooks_path,
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
        min_price=min_price,
        max_price=max_price,
        robust_min_edge=robust_min_edge,
        min_scenario_pass_rate=min_scenario_pass_rate,
        weather_fee_rate=weather_fee_rate,
        max_execution_slippage=max_execution_slippage,
        maker_quote_improvement=maker_quote_improvement,
        maker_min_fill_score=maker_min_fill_score,
        maker_adverse_selection_penalty=maker_adverse_selection_penalty,
        mean_shifts_c=mean_shifts_c,
        sigma_values_c=sigma_values_c,
        slippage_values=slippage_values,
    )


def read_records_csv(path: str | Path) -> list[dict]:
    with Path(path).open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def refresh_open_positions(
    portfolio_path: str | Path = "artifacts/paper_portfolio.csv",
    state_path: str | Path | None = "artifacts/paper_portfolio.json",
    dashboard_path: str | Path | None = "artifacts/paper_dashboard.html",
    stations_path: str | Path = "data/stations.cache.json",
    manual_stations_path: str | Path = "data/manual_stations.csv",
    calibration_path: str | Path = "data/station_calibration.csv",
    targets_path: str | Path = "data/targets.csv",
    default_sigma_c: float = 2.5,
    at_risk_edge: float = 0.0,
    resolve_threshold: float = 0.02,
    bankroll_usdc: float = 100.0,
    now_utc: datetime | None = None,
) -> dict:
    """For each open paper position, re-price the bucket probability using
    today's observed max/min and remaining forecast uncertainty.

    - resolved_early: refined_fair <= resolve_threshold (definitely lost) or
      >= 1 - resolve_threshold (definitely won). PnL is booked on the spot.
    - at_risk: refined_edge < at_risk_edge but not yet decided.
    - still_open: edge intact.

    No orders are placed. In paper we cannot exit at an unknown future price,
    so at_risk positions just get an annotation; only mathematically resolved
    buckets get realized PnL.
    """
    from .paper import (
        render_paper_dashboard,
        summarize_portfolio,
        OPEN_STATUSES,
        SETTLED_STATUSES,
    )

    portfolio_path = Path(portfolio_path)
    if not portfolio_path.exists():
        raise FileNotFoundError(f"paper portfolio missing: {portfolio_path}")

    positions = read_records_csv(portfolio_path)
    calibrations = load_station_calibrations(calibration_path)
    station_catalog = _station_catalog_from_paths(stations_path, manual_stations_path)
    targets_by_slug = _load_targets_by_slug(targets_path)
    refreshed_at = (now_utc or datetime.now(timezone.utc)).isoformat(timespec="seconds")

    # Group open positions by (station_id, target_date) to batch API calls
    open_rows = [p for p in positions if p.get("status") in OPEN_STATUSES]
    fetch_cache: dict[tuple[str, str], dict] = {}

    stats = {"refreshed": 0, "resolved_won": 0, "resolved_lost": 0, "at_risk": 0, "skipped": 0, "errors": 0}

    for position in positions:
        if position.get("status") not in OPEN_STATUSES:
            continue
        target = _derive_target_for_position(position, targets_by_slug)
        if target is None:
            stats["skipped"] += 1
            position["refresh_reason"] = "no target_date on position"
            continue
        station = station_catalog.lookup(target.station_id) if station_catalog else None
        if station is None or station.latitude is None or station.longitude is None:
            stats["skipped"] += 1
            position["refresh_reason"] = f"no coords for station {target.station_id}"
            continue

        key = (target.station_id, target.target_date.isoformat())
        try:
            observation = fetch_cache.get(key)
            if observation is None:
                obs = fetch_intraday_max_min(
                    latitude=station.latitude,
                    longitude=station.longitude,
                    target_date=target.target_date,
                    station_id=target.station_id,
                    now_utc=now_utc,
                )
                observation = {
                    "observed_max_c": obs.observed_max_c,
                    "observed_min_c": obs.observed_min_c,
                    "samples": obs.samples,
                }
                fetch_cache[key] = observation
        except Exception as exc:
            stats["errors"] += 1
            position["refresh_reason"] = f"intraday fetch failed: {exc}"
            continue

        _annotate_refreshed_position(
            position=position,
            target=target,
            observation=observation,
            calibrations=calibrations,
            default_sigma_c=default_sigma_c,
            now_utc=now_utc,
            stats=stats,
            at_risk_edge=at_risk_edge,
            resolve_threshold=resolve_threshold,
        )
        position["refreshed_at"] = refreshed_at

    write_records_csv(positions, portfolio_path)
    summary = summarize_portfolio(positions, bankroll_usdc=bankroll_usdc, generated_at=refreshed_at)
    summary["refresh_stats"] = stats
    payload = {"summary": summary, "positions": positions}
    if state_path:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(state_path).open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    if dashboard_path:
        render_paper_dashboard(payload, dashboard_path)
    return payload


def _station_catalog_from_paths(
    stations_path: str | Path, manual_stations_path: str | Path
) -> object | None:
    from .sources.base import CompositeStationCatalog

    catalogs = []
    if Path(manual_stations_path).exists():
        catalogs.append(ManualStationCatalog(manual_stations_path))
    if Path(stations_path).exists():
        catalogs.append(AviationWeatherStationCatalog(cache_path=stations_path))
    return CompositeStationCatalog(catalogs) if catalogs else None


def _load_targets_by_slug(targets_path: str | Path) -> dict[str, MarketTarget]:
    path = Path(targets_path)
    if not path.exists():
        return {}
    return {t.slug: t for t in read_targets_csv(path)}


def _derive_target_for_position(
    position: dict,
    targets_by_slug: dict[str, MarketTarget] | None = None,
) -> MarketTarget | None:
    """Build a lightweight MarketTarget from a paper row.

    Prefers the canonical targets_by_slug when the row's event_slug matches;
    that gives us station_id and target_date for free. Falls back to parsing
    fields embedded in the paper row.
    """
    slug = str(position.get("event_slug") or "")
    if targets_by_slug and slug in targets_by_slug:
        ref = targets_by_slug[slug]
        if ref.target_date and ref.target_extreme in {"max", "min"} and ref.station_id:
            return ref

    extreme = _extreme_from_position(position)
    if extreme not in {"max", "min"}:
        return None
    end_date_raw = position.get("end_date") or ""
    target_date = _parse_date(end_date_raw) or _parse_date(position.get("target_date") or "")
    station_id = str(position.get("station_id") or "").strip()
    if not target_date or not station_id:
        return None
    return MarketTarget(
        title=str(position.get("event_title") or ""),
        slug=slug,
        city=str(position.get("city") or ""),
        location_name="",
        target_date=target_date,
        target_extreme=extreme,
        target_unit=str(position.get("interval_unit") or "celsius"),
        station_id=station_id,
        resolution_source_url="",
        source_domain="",
        description="",
    )


def _extreme_from_position(position: dict) -> str:
    slug = str(position.get("event_slug") or "").lower()
    if "highest-temperature" in slug:
        return "max"
    if "lowest-temperature" in slug:
        return "min"
    title = str(position.get("event_title") or "").lower()
    if "highest" in title:
        return "max"
    if "lowest" in title:
        return "min"
    return ""


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _annotate_refreshed_position(
    position: dict,
    target: MarketTarget,
    observation: dict,
    calibrations: dict[str, float],
    default_sigma_c: float,
    now_utc: datetime | None,
    stats: dict[str, int],
    at_risk_edge: float,
    resolve_threshold: float,
) -> None:
    """Compute refined_fair, refined_edge, decide resolved_early/at_risk."""
    mean_c = _float_or_none(position.get("prediction_c"))
    if mean_c is None:
        stats["skipped"] += 1
        position["refresh_reason"] = "no prediction_c on position"
        return

    sigma_base = sigma_for_station(target.station_id, calibrations, default_sigma_c)
    interval_unit = str(position.get("interval_unit") or "celsius").lower()
    lower = _float_or_none(position.get("interval_lower"))
    upper = _float_or_none(position.get("interval_upper"))
    if interval_unit == "fahrenheit":
        mean_c_for_calc = (mean_c - 32.0) * 5.0 / 9.0 if mean_c > 60 else mean_c  # interval in F, but mean_c stored in C
    else:
        mean_c_for_calc = mean_c
    # Work fully in market-native units:
    mean_calc = celsius_to_fahrenheit(mean_c) if interval_unit == "fahrenheit" else mean_c
    sigma_calc = sigma_base * 9.0 / 5.0 if interval_unit == "fahrenheit" else sigma_base

    obs_c = observation.get("observed_max_c") if target.target_extreme == "max" else observation.get("observed_min_c")
    observed_native = None
    if obs_c is not None:
        observed_native = celsius_to_fahrenheit(obs_c) if interval_unit == "fahrenheit" else obs_c

    hours_remaining = 24.0
    end_date = _parse_datetime(position.get("end_date") or "")
    if end_date is not None:
        hours_remaining = hours_remaining_until(end_date, now_utc=now_utc)

    spec = NearCloseInput(
        target_extreme=target.target_extreme,
        mean_c=mean_calc,
        sigma_c=sigma_calc,
        lower_c=lower,
        upper_c=upper,
        observed_so_far_c=observed_native,
        hours_remaining=hours_remaining,
    )
    fair_yes_refined = refined_bucket_probability(spec)
    fair_no_refined = 1.0 - fair_yes_refined

    side = position.get("side")
    refined_fair = fair_yes_refined if side == "BUY_YES" else fair_no_refined
    entry_price = _float_or_none(position.get("price")) or 0.0
    entry_fee = _float_or_none(position.get("fee_per_share")) or 0.0
    refined_edge = refined_fair - entry_price - entry_fee

    position["refined_fair_probability"] = round(refined_fair, 6)
    position["refined_edge"] = round(refined_edge, 6)
    position["observed_so_far_c"] = obs_c if obs_c is not None else ""
    position["observed_samples"] = observation.get("samples", 0)
    position["hours_remaining"] = round(hours_remaining, 3)
    position["refresh_reason"] = "refreshed"
    stats["refreshed"] += 1

    stake = _float_or_none(position.get("stake_usdc")) or 0.0
    shares = _float_or_none(position.get("shares")) or 0.0

    # Hard resolve: probability near 0 (we lose) or near 1 (we win)
    if refined_fair <= resolve_threshold:
        position["status"] = "lost"
        position["settled_at"] = (now_utc or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        position["won"] = 0
        position["payout_usdc"] = 0.0
        position["pnl_usdc"] = round(-stake, 4)
        position["roi_pct"] = round(-100.0, 2) if stake else None
        position["actual_status"] = "resolved_early_by_observation"
        stats["resolved_lost"] += 1
        position["refresh_reason"] = (
            f"resolved_early (lost): refined_fair={refined_fair:.4f} <= {resolve_threshold}"
        )
    elif refined_fair >= 1.0 - resolve_threshold:
        payout = shares  # $1/share on a winning Polymarket outcome
        pnl = payout - stake
        position["status"] = "won"
        position["settled_at"] = (now_utc or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        position["won"] = 1
        position["payout_usdc"] = round(payout, 4)
        position["pnl_usdc"] = round(pnl, 4)
        position["roi_pct"] = round((pnl / stake) * 100.0, 2) if stake else None
        position["actual_status"] = "resolved_early_by_observation"
        stats["resolved_won"] += 1
        position["refresh_reason"] = (
            f"resolved_early (won): refined_fair={refined_fair:.4f} >= {1 - resolve_threshold}"
        )
    elif refined_edge < at_risk_edge:
        position["status"] = "at_risk"
        stats["at_risk"] += 1
        position["refresh_reason"] = (
            f"at_risk: refined_edge={refined_edge:.4f} below {at_risk_edge}"
        )
    # else: leave status as-is (still_open)


def _float_or_none(value) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def write_records_csv(records: list[dict], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        if not records:
            fh.write("")
            return
        fieldnames: list[str] = []
        seen = set()
        for record in records:
            for key in record:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def _baseline_predictions(frame: pd.DataFrame) -> pd.Series:
    model = ExtremeTemperatureBaseline()
    return frame.apply(lambda row: model.predict_one(row.to_dict()), axis=1).rename("baseline_prediction_c")


def _records_from_frame(frame: pd.DataFrame) -> list[dict]:
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")


def _write_json(payload: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _write_model_report(summary: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Temperature Model Report",
        "",
        f"- model: `{summary['model_name']}`",
        f"- training rows: `{summary['rows']}`",
        f"- holdout rows: `{summary['holdout_rows']}`",
        f"- train period: `{summary['split']['train_start']}` to `{summary['split']['train_end']}`",
        f"- holdout period: `{summary['split']['test_start']}` to `{summary['split']['test_end']}`",
        f"- features: `{', '.join(summary['feature_columns'])}`",
        "",
        "| split | group | model | samples | MAE C | RMSE C | bias C | within 1C | within 2C | within 3C |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for metric in summary["metrics"]:
        lines.append(
            "| {split} | {group} | {model} | {samples} | {mae_c} | {rmse_c} | {bias_c} | "
            "{within_1c_pct}% | {within_2c_pct}% | {within_3c_pct}% |".format(**metric)
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _convert_to_target_unit(prediction_c: float | None, target_unit: str) -> float | None:
    if prediction_c is None or pd.isna(prediction_c):
        return None
    normalized = "" if target_unit is None or pd.isna(target_unit) else str(target_unit).lower()
    if normalized == "fahrenheit":
        return celsius_to_fahrenheit(prediction_c)
    return prediction_c
