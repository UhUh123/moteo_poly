from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
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
from .sources.actuals import collect_actual_for_target, error_actual_for_target
from .sources.base import ForecastProvider, ObservationProvider, StationCatalog
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
) -> list[dict]:
    targets = read_targets_csv(targets_path)
    station_cache = {}
    records = []
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
            records.append(error_actual_for_target(target, str(exc)).to_record())
            continue
        records.append(actual.to_record())

    write_records_csv(records, output_path)
    return records


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
) -> list[dict]:
    frame = pd.read_csv(features_path)
    model = BiasCorrectedGBM.load(model_path)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    frame["baseline_prediction_c"] = _baseline_predictions(frame)
    frame["corrected_prediction_c"] = model.predict(frame)
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
