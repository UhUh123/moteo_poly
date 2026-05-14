"""Long-running dashboard server for the Windows PC.

Runs `paper_server.run_server` bound to 0.0.0.0:8765 so any host on the
same Tailscale network (including the mac) can hit
  http://<tailscale-ip-of-windows>:8765/
without the user needing to launch anything on the mac.

A Windows firewall rule (installed by register_dashboard_server.ps1)
restricts inbound traffic on this port to the Tailscale CGNAT range
(100.64.0.0/10), so the port is effectively private to our tailnet.

On startup the script logs and writes a `dashboard_server` heartbeat
into status/health.json. Every 5 minutes while running it refreshes
that heartbeat so anyone reading the health file can tell the server
is alive (as opposed to just silent).
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "dashboard_server.log"
HEALTH_PATH = ROOT / "status" / "health.json"

HOST = "0.0.0.0"
PORT = 8765
BANKROLL_USDC = 100.0
HEARTBEAT_INTERVAL_S = 300  # 5 min


def _configure_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("dashboard_server")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _heartbeat_loop(logger: logging.Logger, started_at: float) -> None:
    """Background thread that refreshes the health timestamp every 5 min.

    Note: status.update_task does a shallow merge, not an overwrite, so we
    must explicitly set status="running" here. Earlier versions only set
    "starting" in the one-shot init below; the periodic heartbeat skipped
    the field, and that left a multi-day-old "starting" stuck in the JSON
    even though the server was clearly up. Chapter 7 §10.3 of the learning
    guide flagged this. Fix is one line.
    """
    from detect_temperature.status import update_task

    hostname = socket.gethostname()
    while True:
        try:
            update_task(
                "dashboard_server",
                {
                    "code": 0,
                    "status": "running",
                    "host": HOST,
                    "port": PORT,
                    "hostname": hostname,
                    "uptime_s": round(time.time() - started_at, 2),
                },
                path=HEALTH_PATH,
            )
        except Exception as exc:
            logger.warning(f"heartbeat write failed: {exc}")
        time.sleep(HEARTBEAT_INTERVAL_S)


def main() -> int:
    logger = _configure_logging()
    started_at = time.time()
    logger.info(f"dashboard_server starting on {HOST}:{PORT} (bankroll_usdc={BANKROLL_USDC})")

    # One-shot init heartbeat so health.json sees us immediately even if the
    # first 5-minute tick hasn't fired yet.
    try:
        from detect_temperature.status import update_task
        update_task(
            "dashboard_server",
            {"code": 0, "host": HOST, "port": PORT, "uptime_s": 0.0,
             "hostname": socket.gethostname(), "status": "starting"},
            path=HEALTH_PATH,
            alert=f"dashboard_server started at {HOST}:{PORT}",
        )
    except Exception as exc:
        logger.warning(f"initial heartbeat failed: {exc}")

    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(logger, started_at),
        name="dashboard-heartbeat",
        daemon=True,
    )
    hb_thread.start()

    # Delayed import so logging is already set up if the import itself
    # raises (bad path, missing deps, etc.)
    from detect_temperature.paper_server import run_server

    try:
        run_server(
            host=HOST,
            port=PORT,
            root=ROOT,
            bankroll_usdc=BANKROLL_USDC,
            finalization_lag_days=1,
        )
    except KeyboardInterrupt:
        logger.info("dashboard_server stopped by KeyboardInterrupt")
        return 0
    except Exception as exc:
        logger.exception(f"dashboard_server crashed: {exc}")
        try:
            from detect_temperature.status import update_task
            update_task(
                "dashboard_server",
                {"code": 2, "error": str(exc), "status": "crashed"},
                path=HEALTH_PATH,
                alert=f"dashboard_server CRASHED: {exc}",
            )
        except Exception:
            pass
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
