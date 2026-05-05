from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import (
    build_features,
    build_market_signals,
    build_targets,
    collect_actuals,
    evaluate_resolved_model,
    open_paper_trades,
    open_strategy_paper_trades,
    predict_baseline,
    predict_gbm,
    run_strategy_lab,
    scan_polymarket_weather,
    settle_paper_trades,
    train_gbm_model,
)
from .paper_server import run_server as run_paper_dashboard_server
from .sources.base import CompositeStationCatalog
from .sources.aviation_weather import AviationWeatherMetarProvider, AviationWeatherStationCatalog
from .sources.manual import ManualStationCatalog
from .sources.open_meteo import OpenMeteoForecastProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="detect-temperature")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_targets_parser = subparsers.add_parser("build-targets")
    build_targets_parser.add_argument("--input", default="weather_markets.json")
    build_targets_parser.add_argument("--csv", default="data/targets.csv")
    build_targets_parser.add_argument("--jsonl", default="data/targets.jsonl")
    build_targets_parser.add_argument("--include-unknown", action="store_true")

    refresh_parser = subparsers.add_parser("refresh-stations")
    refresh_parser.add_argument("--output", default="data/stations.cache.json")
    refresh_parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for local certificate issues.")

    build_features_parser = subparsers.add_parser("build-features")
    build_features_parser.add_argument("--targets", default="data/targets.csv")
    build_features_parser.add_argument("--output", default="data/features.csv")
    build_features_parser.add_argument("--stations", default="")
    build_features_parser.add_argument("--manual-stations", default="data/manual_stations.csv")
    build_features_parser.add_argument("--with-open-meteo", action="store_true")
    build_features_parser.add_argument("--with-metar", action="store_true")
    build_features_parser.add_argument("--insecure-avwx", action="store_true", help="Disable TLS verification for AviationWeather calls.")

    predict_parser = subparsers.add_parser("predict-baseline")
    predict_parser.add_argument("--features", default="data/features.csv")
    predict_parser.add_argument("--output", default="artifacts/predictions.csv")

    actuals_parser = subparsers.add_parser("collect-actuals")
    actuals_parser.add_argument("--targets", default="data/targets.csv")
    actuals_parser.add_argument("--output", default="data/actuals.csv")
    actuals_parser.add_argument("--stations", default="data/stations.cache.json")
    actuals_parser.add_argument("--manual-stations", default="data/manual_stations.csv")
    actuals_parser.add_argument("--finalization-lag-days", type=int, default=1)

    train_gbm_parser = subparsers.add_parser("train-gbm")
    train_gbm_parser.add_argument("--training", default="data/training.csv")
    train_gbm_parser.add_argument("--model", default="artifacts/models/gbm.joblib")
    train_gbm_parser.add_argument("--metrics", default="artifacts/model_metrics.json")
    train_gbm_parser.add_argument("--holdout-predictions", default="artifacts/holdout_predictions.csv")
    train_gbm_parser.add_argument("--report", default="artifacts/model_report.md")
    train_gbm_parser.add_argument("--test-fraction", type=float, default=0.33)

    predict_gbm_parser = subparsers.add_parser("predict-gbm")
    predict_gbm_parser.add_argument("--features", default="data/features.csv")
    predict_gbm_parser.add_argument("--model", default="artifacts/models/gbm.joblib")
    predict_gbm_parser.add_argument("--output", default="artifacts/predictions_gbm.csv")

    scan_poly_parser = subparsers.add_parser("scan-polymarket-weather")
    scan_poly_parser.add_argument("--output", default="data/polymarket_weather_markets.csv")
    scan_poly_parser.add_argument("--raw-output", default="data/polymarket_weather_events.json")
    scan_poly_parser.add_argument("--geoblock-output", default="data/polymarket_geoblock.json")

    signals_parser = subparsers.add_parser("build-market-signals")
    signals_parser.add_argument("--markets", default="data/polymarket_weather_markets.csv")
    signals_parser.add_argument("--predictions", default="artifacts/predictions_gbm.csv")
    signals_parser.add_argument("--output", default="artifacts/market_signals.csv")
    signals_parser.add_argument("--sigma-c", type=float, default=1.5)
    signals_parser.add_argument("--min-edge", type=float, default=0.03)
    signals_parser.add_argument("--weather-fee-rate", type=float, default=0.05)
    signals_parser.add_argument("--bankroll-usdc", type=float, default=None)
    signals_parser.add_argument("--min-yes-probability", type=float, default=0.08)
    signals_parser.add_argument("--min-no-probability", type=float, default=0.55)
    signals_parser.add_argument("--max-spread", type=float, default=0.08)
    signals_parser.add_argument("--min-liquidity", type=float, default=0.0)
    signals_parser.add_argument("--allow-no-on-top-bucket", action="store_true")
    signals_parser.add_argument("--near-top-no-guard-ratio", type=float, default=0.75)

    open_paper_parser = subparsers.add_parser("open-paper-trades")
    open_paper_parser.add_argument("--signals", default="artifacts/market_signals.csv")
    open_paper_parser.add_argument("--output", default="artifacts/paper_portfolio.csv")
    open_paper_parser.add_argument("--state", default="artifacts/paper_portfolio.json")
    open_paper_parser.add_argument("--dashboard", default="artifacts/paper_dashboard.html")
    open_paper_parser.add_argument("--bankroll-usdc", type=float, default=1000.0)
    open_paper_parser.add_argument("--min-edge", type=float, default=0.03)
    open_paper_parser.add_argument("--max-positions", type=int, default=100)
    open_paper_parser.add_argument("--max-stake-usdc", type=float, default=5.0)
    open_paper_parser.add_argument("--max-total-exposure-pct", type=float, default=0.5)
    open_paper_parser.add_argument("--max-event-exposure-pct", type=float, default=0.05)
    open_paper_parser.add_argument("--min-price", type=float, default=0.005)
    open_paper_parser.add_argument("--max-price", type=float, default=0.97)
    open_paper_parser.add_argument("--allow-ended", action="store_true")

    open_strategy_paper_parser = subparsers.add_parser("open-strategy-paper-trades")
    open_strategy_paper_parser.add_argument("--strategy-portfolio", default="artifacts/strategy_portfolio_v2.csv")
    open_strategy_paper_parser.add_argument("--output", default="artifacts/paper_portfolio.csv")
    open_strategy_paper_parser.add_argument("--state", default="artifacts/paper_portfolio.json")
    open_strategy_paper_parser.add_argument("--dashboard", default="artifacts/paper_dashboard.html")
    open_strategy_paper_parser.add_argument("--bankroll-usdc", type=float, default=1000.0)
    open_strategy_paper_parser.add_argument("--max-positions", type=int, default=100)
    open_strategy_paper_parser.add_argument(
        "--execution-mode",
        choices=("taker", "maker-preferred", "maker-only"),
        default="taker",
    )
    open_strategy_paper_parser.add_argument("--weather-fee-rate", type=float, default=0.05)
    open_strategy_paper_parser.add_argument("--maker-fee-rate", type=float, default=0.0)

    settle_paper_parser = subparsers.add_parser("settle-paper-trades")
    settle_paper_parser.add_argument("--portfolio", default="artifacts/paper_portfolio.csv")
    settle_paper_parser.add_argument("--actuals", default="data/actuals.csv")
    settle_paper_parser.add_argument("--output", default="artifacts/paper_portfolio_settled.csv")
    settle_paper_parser.add_argument("--state", default="artifacts/paper_portfolio_settled.json")
    settle_paper_parser.add_argument("--dashboard", default="artifacts/paper_dashboard.html")
    settle_paper_parser.add_argument("--bankroll-usdc", type=float, default=1000.0)

    resolved_parser = subparsers.add_parser("evaluate-resolved-model")
    resolved_parser.add_argument("--predictions", default="artifacts/predictions_gbm.csv")
    resolved_parser.add_argument("--actuals", default="data/actuals.csv")
    resolved_parser.add_argument("--signals", default="artifacts/market_signals.csv")
    resolved_parser.add_argument("--events-output", default="artifacts/resolved_model_events.csv")
    resolved_parser.add_argument("--signals-output", default="artifacts/resolved_model_signals.csv")
    resolved_parser.add_argument("--report", default="artifacts/resolved_model_report.html")

    strategy_lab_parser = subparsers.add_parser("run-strategy-lab")
    strategy_lab_parser.add_argument("--signals", default="artifacts/market_signals.csv")
    strategy_lab_parser.add_argument("--candidates-output", default="artifacts/strategy_candidates_v2.csv")
    strategy_lab_parser.add_argument("--portfolio-output", default="artifacts/strategy_portfolio_v2.csv")
    strategy_lab_parser.add_argument("--summary-output", default="artifacts/strategy_lab_summary.json")
    strategy_lab_parser.add_argument("--report", default="artifacts/strategy_lab_report.html")
    strategy_lab_parser.add_argument("--bankroll-usdc", type=float, default=1000.0)
    strategy_lab_parser.add_argument("--max-positions", type=int, default=100)
    strategy_lab_parser.add_argument("--max-stake-usdc", type=float, default=5.0)
    strategy_lab_parser.add_argument("--max-total-exposure-pct", type=float, default=0.5)
    strategy_lab_parser.add_argument("--max-event-exposure-pct", type=float, default=0.05)
    strategy_lab_parser.add_argument("--max-event-positions", type=int, default=2)
    strategy_lab_parser.add_argument("--max-city-positions", type=int, default=4)
    strategy_lab_parser.add_argument("--max-city-exposure-pct", type=float, default=0.08)
    strategy_lab_parser.add_argument("--max-date-exposure-pct", type=float, default=0.30)
    strategy_lab_parser.add_argument("--max-extreme-exposure-pct", type=float, default=0.35)
    strategy_lab_parser.add_argument("--min-price", type=float, default=0.005)
    strategy_lab_parser.add_argument("--max-price", type=float, default=0.97)
    strategy_lab_parser.add_argument("--robust-min-edge", type=float, default=0.01)
    strategy_lab_parser.add_argument("--min-scenario-pass-rate", type=float, default=1.0)
    strategy_lab_parser.add_argument("--weather-fee-rate", type=float, default=0.05)
    strategy_lab_parser.add_argument("--max-execution-slippage", type=float, default=0.02)
    strategy_lab_parser.add_argument("--maker-quote-improvement", type=float, default=0.005)
    strategy_lab_parser.add_argument("--maker-min-fill-score", type=float, default=0.35)
    strategy_lab_parser.add_argument("--maker-adverse-selection-penalty", type=float, default=0.01)
    strategy_lab_parser.add_argument("--mean-shifts-c", default="-1,0,1")
    strategy_lab_parser.add_argument("--sigma-values-c", default="1.5,2.0,2.5")
    strategy_lab_parser.add_argument("--slippage-values", default="0,0.01")

    serve_paper_parser = subparsers.add_parser("serve-paper-dashboard")
    serve_paper_parser.add_argument("--host", default="127.0.0.1")
    serve_paper_parser.add_argument("--port", type=int, default=8765)
    serve_paper_parser.add_argument("--root", default=".")
    serve_paper_parser.add_argument("--bankroll-usdc", type=float, default=1000.0)
    serve_paper_parser.add_argument("--finalization-lag-days", type=int, default=1)

    args = parser.parse_args(argv)

    if args.command == "build-targets":
        targets = build_targets(
            input_path=args.input,
            csv_path=args.csv,
            jsonl_path=args.jsonl,
            include_unknown=args.include_unknown,
        )
        print(f"built {len(targets)} targets -> {args.csv}")
        return 0

    if args.command == "refresh-stations":
        catalog = AviationWeatherStationCatalog(cache_path=args.output, verify_tls=not args.insecure)
        path = catalog.refresh_cache()
        print(f"station cache refreshed -> {path}")
        return 0

    if args.command == "build-features":
        catalogs = []
        if args.manual_stations and Path(args.manual_stations).exists():
            catalogs.append(ManualStationCatalog(args.manual_stations))
        if args.stations:
            catalogs.append(AviationWeatherStationCatalog(cache_path=args.stations))
        station_catalog = CompositeStationCatalog(catalogs) if catalogs else None
        forecast_provider = OpenMeteoForecastProvider() if args.with_open_meteo else None
        observation_provider = (
            AviationWeatherMetarProvider(verify_tls=not args.insecure_avwx) if args.with_metar else None
        )
        rows = build_features(
            targets_path=args.targets,
            output_path=args.output,
            station_catalog=station_catalog,
            forecast_provider=forecast_provider,
            observation_provider=observation_provider,
        )
        print(f"built {len(rows)} feature rows -> {args.output}")
        return 0

    if args.command == "predict-baseline":
        predictions = predict_baseline(features_path=args.features, output_path=args.output)
        available = sum(1 for row in predictions if row.get("prediction_c") not in {"", None})
        print(f"wrote {len(predictions)} rows, {available} with baseline predictions -> {args.output}")
        return 0

    if args.command == "collect-actuals":
        catalogs = []
        if args.manual_stations and Path(args.manual_stations).exists():
            catalogs.append(ManualStationCatalog(args.manual_stations))
        if args.stations and Path(args.stations).exists():
            catalogs.append(AviationWeatherStationCatalog(cache_path=args.stations))
        station_catalog = CompositeStationCatalog(catalogs) if catalogs else None
        rows = collect_actuals(
            targets_path=args.targets,
            output_path=args.output,
            station_catalog=station_catalog,
            finalization_lag_days=args.finalization_lag_days,
        )
        ok = sum(1 for row in rows if row.get("status") == "ok")
        pending = sum(1 for row in rows if row.get("status") == "pending")
        errors = sum(1 for row in rows if row.get("status") == "error")
        print(f"collected actuals -> ok={ok}, pending={pending}, error={errors} -> {args.output}")
        return 0

    if args.command == "train-gbm":
        summary = train_gbm_model(
            training_path=args.training,
            model_path=args.model,
            metrics_path=args.metrics,
            holdout_predictions_path=args.holdout_predictions,
            report_path=args.report,
            test_fraction=args.test_fraction,
        )
        combined = [
            metric for metric in summary["metrics"]
            if metric["group"] == "combined" and metric["model"] == "sklearn_hist_gradient_boosting_bias_corrector"
        ]
        suffix = ""
        if combined:
            metric = combined[0]
            suffix = f", holdout MAE={metric['mae_c']}C, within_2C={metric['within_2c_pct']}%"
        print(f"trained GBM on {summary['rows']} rows -> {args.model}{suffix}")
        return 0

    if args.command == "predict-gbm":
        rows = predict_gbm(features_path=args.features, model_path=args.model, output_path=args.output)
        available = sum(1 for row in rows if row.get("corrected_prediction_c") not in {"", None})
        print(f"wrote {len(rows)} rows, {available} with corrected predictions -> {args.output}")
        return 0

    if args.command == "scan-polymarket-weather":
        rows = scan_polymarket_weather(
            output_path=args.output,
            raw_output_path=args.raw_output,
            geoblock_output_path=args.geoblock_output,
        )
        active = sum(1 for row in rows if row.get("active") in {1, "1", True})
        print(f"scanned {len(rows)} temperature markets, active={active} -> {args.output}")
        return 0

    if args.command == "build-market-signals":
        rows = build_market_signals(
            markets_path=args.markets,
            predictions_path=args.predictions,
            output_path=args.output,
            sigma_c=args.sigma_c,
            min_edge=args.min_edge,
            weather_fee_rate=args.weather_fee_rate,
            bankroll_usdc=args.bankroll_usdc,
            min_yes_probability=args.min_yes_probability,
            min_no_probability=args.min_no_probability,
            max_spread=args.max_spread,
            min_liquidity=args.min_liquidity,
            guard_no_on_top_bucket=not args.allow_no_on_top_bucket,
            near_top_no_guard_ratio=args.near_top_no_guard_ratio,
        )
        matched = sum(1 for row in rows if row.get("matched_prediction_slug"))
        trades = sum(1 for row in rows if row.get("paper_side") in {"BUY_YES", "BUY_NO"})
        print(f"built {len(rows)} paper signals, matched={matched}, trades={trades} -> {args.output}")
        return 0

    if args.command == "open-paper-trades":
        payload = open_paper_trades(
            signals_path=args.signals,
            output_path=args.output,
            state_path=args.state,
            dashboard_path=args.dashboard,
            bankroll_usdc=args.bankroll_usdc,
            min_edge=args.min_edge,
            max_positions=args.max_positions,
            max_stake_usdc=args.max_stake_usdc,
            max_total_exposure_pct=args.max_total_exposure_pct,
            max_event_exposure_pct=args.max_event_exposure_pct,
            min_price=args.min_price,
            max_price=args.max_price,
            allow_ended=args.allow_ended,
        )
        summary = payload["summary"]
        print(
            "opened paper portfolio -> "
            f"positions={summary['positions']}, staked={summary['total_staked_usdc']} USDC, "
            f"expected_pnl={summary['expected_total_pnl_usdc']} USDC -> {args.dashboard}"
        )
        return 0

    if args.command == "open-strategy-paper-trades":
        payload = open_strategy_paper_trades(
            strategy_portfolio_path=args.strategy_portfolio,
            output_path=args.output,
            state_path=args.state,
            dashboard_path=args.dashboard,
            bankroll_usdc=args.bankroll_usdc,
            max_positions=args.max_positions,
            execution_mode=args.execution_mode,
            weather_fee_rate=args.weather_fee_rate,
            maker_fee_rate=args.maker_fee_rate,
        )
        summary = payload["summary"]
        print(
            "opened strategy paper portfolio -> "
            f"positions={summary['positions']}, staked={summary['total_staked_usdc']} USDC, "
            f"expected_pnl={summary['expected_total_pnl_usdc']} USDC, "
            f"entry_mix=taker:{summary['taker_positions']}/maker:{summary['maker_positions']} -> {args.dashboard}"
        )
        return 0

    if args.command == "settle-paper-trades":
        payload = settle_paper_trades(
            portfolio_path=args.portfolio,
            actuals_path=args.actuals,
            output_path=args.output,
            state_path=args.state,
            dashboard_path=args.dashboard,
            bankroll_usdc=args.bankroll_usdc,
        )
        summary = payload["summary"]
        print(
            "settled paper portfolio -> "
            f"settled={summary['settled_positions']}, open={summary['open_positions']}, "
            f"realized_pnl={summary['realized_pnl_usdc']} USDC -> {args.dashboard}"
        )
        return 0

    if args.command == "evaluate-resolved-model":
        payload = evaluate_resolved_model(
            predictions_path=args.predictions,
            actuals_path=args.actuals,
            signals_path=args.signals,
            event_output_path=args.events_output,
            signal_output_path=args.signals_output,
            report_path=args.report,
        )
        summary = payload["summary"]
        signal_win_rate = (
            f"{summary['signal_win_rate_pct']}%"
            if summary["signal_win_rate_pct"] is not None
            else "-"
        )
        print(
            "evaluated resolved model -> "
            f"events={summary['resolved_events']}, "
            f"MAE={summary['mae_resolution']} {summary['primary_unit']}, "
            f"rounded_exact={summary['rounded_exact_pct']}%, "
            f"within_1_unit={summary['within_1_unit_pct']}%, "
            f"within_2_units={summary['within_2_units_pct']}%, "
            f"signal_win_rate={signal_win_rate} -> {args.report}"
        )
        return 0

    if args.command == "run-strategy-lab":
        payload = run_strategy_lab(
            signals_path=args.signals,
            candidates_output_path=args.candidates_output,
            portfolio_output_path=args.portfolio_output,
            summary_output_path=args.summary_output,
            report_path=args.report,
            bankroll_usdc=args.bankroll_usdc,
            max_positions=args.max_positions,
            max_stake_usdc=args.max_stake_usdc,
            max_total_exposure_pct=args.max_total_exposure_pct,
            max_event_exposure_pct=args.max_event_exposure_pct,
            max_event_positions=args.max_event_positions,
            max_city_positions=args.max_city_positions,
            max_city_exposure_pct=args.max_city_exposure_pct,
            max_date_exposure_pct=args.max_date_exposure_pct,
            max_extreme_exposure_pct=args.max_extreme_exposure_pct,
            min_price=args.min_price,
            max_price=args.max_price,
            robust_min_edge=args.robust_min_edge,
            min_scenario_pass_rate=args.min_scenario_pass_rate,
            weather_fee_rate=args.weather_fee_rate,
            max_execution_slippage=args.max_execution_slippage,
            maker_quote_improvement=args.maker_quote_improvement,
            maker_min_fill_score=args.maker_min_fill_score,
            maker_adverse_selection_penalty=args.maker_adverse_selection_penalty,
            mean_shifts_c=_parse_float_tuple(args.mean_shifts_c),
            sigma_values_c=_parse_float_tuple(args.sigma_values_c),
            slippage_values=_parse_float_tuple(args.slippage_values),
        )
        summary = payload["summary"]
        print(
            "strategy lab -> "
            f"candidates={summary['trade_candidates']}, robust={summary['robust_pass']}, "
            f"selected={summary['selected_positions']}, stake={summary['selected_stake_usdc']} USDC, "
            f"exec_expected_pnl={summary['selected_execution_expected_pnl_usdc']} USDC, "
            f"maker_preferred={summary['selected_maker_preferred']} -> {args.report}"
        )
        return 0

    if args.command == "serve-paper-dashboard":
        run_paper_dashboard_server(
            host=args.host,
            port=args.port,
            root=args.root,
            bankroll_usdc=args.bankroll_usdc,
            finalization_lag_days=args.finalization_lag_days,
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _parse_float_tuple(value: str) -> tuple[float, ...]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise argparse.ArgumentTypeError("expected at least one comma-separated float")
    return tuple(float(item) for item in items)


if __name__ == "__main__":
    raise SystemExit(main())
