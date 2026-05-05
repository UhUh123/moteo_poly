from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .paper import render_paper_dashboard
from .pipeline import collect_actuals, settle_paper_trades
from .sources.aviation_weather import AviationWeatherStationCatalog
from .sources.base import CompositeStationCatalog
from .sources.manual import ManualStationCatalog


DEFAULT_BANKROLL_USDC = 1000.0


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
            if self.path != "/api/refresh-paper":
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
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
