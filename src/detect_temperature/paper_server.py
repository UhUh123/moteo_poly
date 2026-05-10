from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .paper import render_paper_dashboard
from .pipeline import (
    build_features,
    build_market_signals,
    build_polymarket_targets,
    collect_actuals,
    fetch_clob_orderbooks,
    open_strategy_paper_trades,
    predict_gbm,
    run_strategy_lab,
    scan_polymarket_weather,
    settle_paper_trades,
)
from .risk_guards import DrawdownAbort, check_drawdown
from .risk_profiles import risk_profile_values
from .sources.aviation_weather import AviationWeatherStationCatalog
from .sources.base import CompositeStationCatalog
from .sources.manual import ManualStationCatalog
from .sources.open_meteo import OpenMeteoForecastProvider


DEFAULT_BANKROLL_USDC = 1000.0

ARCHIVABLE_FILES: tuple[tuple[str, str], ...] = (
    ("artifacts/market_signals.csv", "market_signals.csv"),
    ("artifacts/strategy_candidates_v2.csv", "strategy_candidates_v2.csv"),
    ("artifacts/strategy_portfolio_v2.csv", "strategy_portfolio_v2.csv"),
    ("artifacts/strategy_lab_summary.json", "strategy_lab_summary.json"),
    ("artifacts/strategy_lab_report.html", "strategy_lab_report.html"),
    ("artifacts/paper_portfolio.csv", "paper_portfolio.csv"),
    ("artifacts/paper_portfolio.json", "paper_portfolio.json"),
    ("artifacts/paper_dashboard.html", "paper_dashboard.html"),
    ("artifacts/predictions_gbm.csv", "predictions_gbm.csv"),
    ("data/polymarket_weather_markets.csv", "polymarket_weather_markets.csv"),
    ("data/targets.csv", "targets.csv"),
    ("data/features.csv", "features.csv"),
)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    root: str | Path | None = None,
    bankroll_usdc: float = DEFAULT_BANKROLL_USDC,
    finalization_lag_days: int = 1,
) -> None:
    project_root = Path(root or Path.cwd()).resolve()
    handler = _make_handler(
        project_root=project_root,
        bankroll_usdc=bankroll_usdc,
        finalization_lag_days=finalization_lag_days,
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Paper dashboard: http://{host}:{port}/")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serve-paper-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", default=".")
    parser.add_argument("--bankroll-usdc", type=float, default=DEFAULT_BANKROLL_USDC)
    parser.add_argument("--finalization-lag-days", type=int, default=1)
    args = parser.parse_args(argv)
    run_server(
        host=args.host,
        port=args.port,
        root=args.root,
        bankroll_usdc=args.bankroll_usdc,
        finalization_lag_days=args.finalization_lag_days,
    )
    return 0


def refresh_paper_state(
    project_root: Path,
    bankroll_usdc: float = DEFAULT_BANKROLL_USDC,
    finalization_lag_days: int = 1,
) -> dict[str, Any]:
    actuals_path = project_root / "data" / "actuals.csv"
    actual_rows = collect_actuals(
        targets_path=project_root / "data" / "targets.csv",
        output_path=actuals_path,
        station_catalog=_station_catalog(project_root),
        finalization_lag_days=finalization_lag_days,
    )
    payload = settle_paper_trades(
        portfolio_path=project_root / "artifacts" / "paper_portfolio.csv",
        actuals_path=actuals_path,
        output_path=project_root / "artifacts" / "paper_portfolio.csv",
        state_path=project_root / "artifacts" / "paper_portfolio.json",
        dashboard_path=project_root / "artifacts" / "paper_dashboard.html",
        bankroll_usdc=bankroll_usdc,
    )
    payload["actuals"] = {
        "ok": sum(1 for row in actual_rows if row.get("status") == "ok"),
        "pending": sum(1 for row in actual_rows if row.get("status") == "pending"),
        "error": sum(1 for row in actual_rows if row.get("status") == "error"),
        "path": str(actuals_path),
    }
    render_paper_dashboard(payload, project_root / "artifacts" / "paper_dashboard.html")
    with (project_root / "artifacts" / "paper_portfolio.json").open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return payload


def _archive_current_run(project_root: Path, label: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = project_root / "artifacts" / "paper_runs" / f"{timestamp}-{label}"
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src_rel, filename in ARCHIVABLE_FILES:
        src = project_root / src_rel
        if not src.exists():
            continue
        shutil.copy2(src, dest / filename)
        copied.append(filename)
    (dest / "ARCHIVE_MANIFEST.json").write_text(
        json.dumps({"timestamp_utc": timestamp, "label": label, "files": copied}, indent=2),
        encoding="utf-8",
    )
    return dest


def _profile_flag(profile_name: str, command: str, key: str, fallback: Any) -> Any:
    values = risk_profile_values(profile_name, command)
    return values.get(key, fallback)


def _parse_tuple(value: Any, fallback: tuple[float, ...]) -> tuple[float, ...]:
    if value is None:
        return fallback
    if isinstance(value, (tuple, list)):
        return tuple(float(item) for item in value)
    if isinstance(value, str):
        return tuple(float(item.strip()) for item in value.split(",") if item.strip())
    return fallback


def run_market_pipeline(
    project_root: Path,
    risk_profile: str,
    bankroll_usdc: float,
    with_open_meteo: bool = True,
) -> dict[str, Any]:
    """Refresh markets + features + predictions + signals + strategy lab.

    Does NOT open paper positions. Returns Strategy Lab summary.
    """
    scan_payload = scan_polymarket_weather(
        output_path=project_root / "data" / "polymarket_weather_markets.csv",
        raw_output_path=project_root / "data" / "polymarket_weather_events.json",
        geoblock_output_path=project_root / "data" / "polymarket_geoblock.json",
    )
    build_polymarket_targets(
        events_path=project_root / "data" / "polymarket_weather_events.json",
        csv_path=project_root / "data" / "targets.csv",
        jsonl_path=project_root / "data" / "targets.jsonl",
        reference_targets_path=project_root / "data" / "targets.csv",
    )
    station_catalog = _station_catalog(project_root)
    forecast_provider = OpenMeteoForecastProvider() if with_open_meteo else None
    build_features(
        targets_path=project_root / "data" / "targets.csv",
        output_path=project_root / "data" / "features.csv",
        station_catalog=station_catalog,
        forecast_provider=forecast_provider,
    )
    predict_gbm(
        features_path=project_root / "data" / "features.csv",
        model_path=project_root / "artifacts" / "models" / "gbm.joblib",
        output_path=project_root / "artifacts" / "predictions_gbm.csv",
    )

    sigma_c = float(_profile_flag(risk_profile, "build-market-signals", "sigma_c", 1.5))
    min_edge = float(_profile_flag(risk_profile, "build-market-signals", "min_edge", 0.03))
    min_yes = float(_profile_flag(risk_profile, "build-market-signals", "min_yes_probability", 0.08))
    min_no = float(_profile_flag(risk_profile, "build-market-signals", "min_no_probability", 0.55))
    max_spread = float(_profile_flag(risk_profile, "build-market-signals", "max_spread", 0.08))
    min_liquidity = float(_profile_flag(risk_profile, "build-market-signals", "min_liquidity", 0.0))
    allow_buy_yes = bool(_profile_flag(risk_profile, "build-market-signals", "allow_buy_yes", True))
    build_market_signals(
        markets_path=project_root / "data" / "polymarket_weather_markets.csv",
        predictions_path=project_root / "artifacts" / "predictions_gbm.csv",
        output_path=project_root / "artifacts" / "market_signals.csv",
        sigma_c=sigma_c,
        min_edge=min_edge,
        weather_fee_rate=0.05,
        bankroll_usdc=bankroll_usdc,
        min_yes_probability=min_yes,
        min_no_probability=min_no,
        max_spread=max_spread,
        min_liquidity=min_liquidity,
        allow_buy_yes=allow_buy_yes,
    )

    fetch_clob_orderbooks(
        markets_path=project_root / "data" / "polymarket_weather_markets.csv",
        output_path=project_root / "data" / "polymarket_orderbooks.json",
    )

    lab_profile = risk_profile_values(risk_profile, "run-strategy-lab")
    strategy_payload = run_strategy_lab(
        signals_path=project_root / "artifacts" / "market_signals.csv",
        candidates_output_path=project_root / "artifacts" / "strategy_candidates_v2.csv",
        portfolio_output_path=project_root / "artifacts" / "strategy_portfolio_v2.csv",
        summary_output_path=project_root / "artifacts" / "strategy_lab_summary.json",
        report_path=project_root / "artifacts" / "strategy_lab_report.html",
        orderbooks_path=project_root / "data" / "polymarket_orderbooks.json",
        bankroll_usdc=float(lab_profile.get("bankroll_usdc", bankroll_usdc)),
        max_positions=int(lab_profile.get("max_positions", 100)),
        max_stake_usdc=float(lab_profile.get("max_stake_usdc", 5.0)),
        max_total_exposure_pct=float(lab_profile.get("max_total_exposure_pct", 0.5)),
        max_event_exposure_pct=float(lab_profile.get("max_event_exposure_pct", 0.05)),
        max_event_positions=int(lab_profile.get("max_event_positions", 2)),
        max_city_positions=int(lab_profile.get("max_city_positions", 4)),
        max_city_exposure_pct=float(lab_profile.get("max_city_exposure_pct", 0.08)),
        max_date_exposure_pct=float(lab_profile.get("max_date_exposure_pct", 0.30)),
        max_extreme_exposure_pct=float(lab_profile.get("max_extreme_exposure_pct", 0.35)),
        min_price=float(lab_profile.get("min_price", 0.005)),
        max_price=float(lab_profile.get("max_price", 0.97)),
        robust_min_edge=float(lab_profile.get("robust_min_edge", 0.01)),
        min_scenario_pass_rate=float(lab_profile.get("min_scenario_pass_rate", 1.0)),
        weather_fee_rate=float(lab_profile.get("weather_fee_rate", 0.05)),
        max_execution_slippage=float(lab_profile.get("max_execution_slippage", 0.02)),
        maker_quote_improvement=float(lab_profile.get("maker_quote_improvement", 0.005)),
        maker_min_fill_score=float(lab_profile.get("maker_min_fill_score", 0.35)),
        maker_adverse_selection_penalty=float(lab_profile.get("maker_adverse_selection_penalty", 0.01)),
        mean_shifts_c=_parse_tuple(lab_profile.get("mean_shifts_c"), (-1.0, 0.0, 1.0)),
        sigma_values_c=_parse_tuple(lab_profile.get("sigma_values_c"), (1.5, 2.0, 2.5)),
        slippage_values=_parse_tuple(lab_profile.get("slippage_values"), (0.0, 0.01)),
    )

    return {
        "market_rows": len(scan_payload),
        "strategy_lab": strategy_payload["summary"],
    }


def run_dry_run_pipeline(
    project_root: Path,
    risk_profile: str = "bankroll_100",
    bankroll_usdc: float = 100.0,
) -> dict[str, Any]:
    """Full scan + signals + strategy lab, NO paper open. Archives nothing new."""
    _archive_current_run(project_root, label="dry-run")
    return run_market_pipeline(project_root, risk_profile=risk_profile, bankroll_usdc=bankroll_usdc)


def run_open_trades_pipeline(
    project_root: Path,
    risk_profile: str = "bankroll_100",
    bankroll_usdc: float = 100.0,
    finalization_lag_days: int = 1,
) -> dict[str, Any]:
    """Full pipeline + open paper portfolio. Archives previous run first."""
    archive_dir = _archive_current_run(project_root, label="pre-open")

    drawdown_limit = _profile_flag(
        risk_profile, "open-strategy-paper-trades", "drawdown_abort_usdc", None
    )
    if drawdown_limit is not None:
        check_drawdown(
            state_paths=[
                project_root / "artifacts" / "paper_portfolio_settled.json",
                project_root / "artifacts" / "paper_portfolio.json",
            ],
            abort_usdc=float(drawdown_limit),
        )

    market_result = run_market_pipeline(
        project_root, risk_profile=risk_profile, bankroll_usdc=bankroll_usdc
    )

    open_profile = risk_profile_values(risk_profile, "open-strategy-paper-trades")
    paper_payload = open_strategy_paper_trades(
        strategy_portfolio_path=project_root / "artifacts" / "strategy_portfolio_v2.csv",
        output_path=project_root / "artifacts" / "paper_portfolio.csv",
        state_path=project_root / "artifacts" / "paper_portfolio.json",
        dashboard_path=project_root / "artifacts" / "paper_dashboard.html",
        bankroll_usdc=float(open_profile.get("bankroll_usdc", bankroll_usdc)),
        max_positions=int(open_profile.get("max_positions", 100)),
        execution_mode=str(open_profile.get("execution_mode", "taker")),
        weather_fee_rate=float(open_profile.get("weather_fee_rate", 0.05)),
        maker_fee_rate=float(open_profile.get("maker_fee_rate", 0.0)),
    )

    try:
        refresh_paper_state(
            project_root,
            bankroll_usdc=bankroll_usdc,
            finalization_lag_days=finalization_lag_days,
        )
    except Exception:
        pass

    return {
        "archive_dir": str(archive_dir),
        "market_pipeline": market_result,
        "paper_summary": paper_payload["summary"],
    }


def _station_catalog(project_root: Path) -> CompositeStationCatalog | None:
    catalogs = []
    manual_path = project_root / "data" / "manual_stations.csv"
    if manual_path.exists():
        catalogs.append(ManualStationCatalog(manual_path))
    stations_path = project_root / "data" / "stations.cache.json"
    if stations_path.exists():
        catalogs.append(AviationWeatherStationCatalog(cache_path=stations_path))
    return CompositeStationCatalog(catalogs) if catalogs else None


def _make_handler(project_root: Path, bankroll_usdc: float, finalization_lag_days: int):
    class PaperDashboardHandler(BaseHTTPRequestHandler):
        server_version = "PaperWeatherDashboard/0.1"

        def do_GET(self) -> None:
            if self.path in {"/", "/dashboard", "/paper_dashboard.html"}:
                self._serve_dashboard()
                return
            if self.path == "/api/status":
                self._send_json(_load_state(project_root, bankroll_usdc))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_HEAD(self) -> None:
            if self.path not in {"/", "/dashboard", "/paper_dashboard.html"}:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            dashboard_path = project_root / "artifacts" / "paper_dashboard.html"
            size = dashboard_path.stat().st_size if dashboard_path.exists() else 0
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_POST(self) -> None:
            handlers = {
                "/api/refresh-paper": self._handle_refresh,
                "/api/open-trades": self._handle_open_trades,
                "/api/dry-run": self._handle_dry_run,
            }
            handler = handlers.get(self.path)
            if handler is None:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            handler()

        def _handle_refresh(self) -> None:
            try:
                payload = refresh_paper_state(
                    project_root=project_root,
                    bankroll_usdc=bankroll_usdc,
                    finalization_lag_days=finalization_lag_days,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json(payload)

        def _handle_open_trades(self) -> None:
            try:
                payload = run_open_trades_pipeline(
                    project_root=project_root,
                    risk_profile="bankroll_100",
                    bankroll_usdc=100.0,
                    finalization_lag_days=finalization_lag_days,
                )
            except DrawdownAbort as exc:
                self._send_json(
                    {"error": str(exc), "kind": "drawdown"},
                    status=HTTPStatus.CONFLICT,
                )
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json(payload)

        def _handle_dry_run(self) -> None:
            try:
                payload = run_dry_run_pipeline(
                    project_root=project_root,
                    risk_profile="bankroll_100",
                    bankroll_usdc=100.0,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json(payload)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"{self.address_string()} - {format % args}")

        def _serve_dashboard(self) -> None:
            dashboard_path = project_root / "artifacts" / "paper_dashboard.html"
            if not dashboard_path.exists():
                state = _load_state(project_root, bankroll_usdc)
                render_paper_dashboard(state, dashboard_path)
            body = dashboard_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return PaperDashboardHandler


def _load_state(project_root: Path, bankroll_usdc: float) -> dict[str, Any]:
    for path in (
        project_root / "artifacts" / "paper_portfolio.json",
        project_root / "artifacts" / "paper_portfolio_settled.json",
    ):
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
    return {
        "summary": {
            "bankroll_usdc": bankroll_usdc,
            "positions": 0,
            "open_positions": 0,
            "settled_positions": 0,
            "realized_pnl_usdc": 0,
            "expected_total_pnl_usdc": 0,
        },
        "positions": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
