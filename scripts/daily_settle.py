"""Daily automated settle cycle.

Runs collect-actuals + settle-paper-trades for the current paper portfolio.
Intended for Windows Task Scheduler at 06:00 UTC, ~8 hours after the last
North-American weather market closes, so Wunderground / HKO / Synoptic
have the final observations published.

Exit codes:
    0 - success
    2 - pipeline error (e.g. actuals source down)
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "daily_settle.log"
HEALTH_PATH = ROOT / "status" / "health.json"


def _configure_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("daily_settle")
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


def main() -> int:
    logger = _configure_logging()
    started = time.time()
    logger.info("daily_settle start")

    from detect_temperature.paper_server import refresh_paper_state
    from detect_temperature.status import update_task

    portfolio_path = ROOT / "artifacts" / "paper_portfolio.csv"
    if not portfolio_path.exists():
        logger.info("paper_portfolio.csv missing — nothing to settle yet")
        update_task(
            "daily_settle",
            {"code": 0, "outcome": "skip_no_portfolio"},
            path=HEALTH_PATH,
            alert="skip: no paper portfolio yet",
        )
        return 0

    try:
        payload = refresh_paper_state(
            project_root=ROOT,
            bankroll_usdc=100.0,
            finalization_lag_days=1,
        )
    except Exception as exc:
        logger.exception(f"daily_settle failed: {exc}")
        update_task(
            "daily_settle",
            {"code": 2, "outcome": "error", "error": str(exc)},
            path=HEALTH_PATH,
            alert=f"ERROR: {exc}",
        )
        return 2

    summary = payload.get("summary", {}) or {}
    actuals = payload.get("actuals", {}) or {}
    elapsed = time.time() - started
    logger.info(
        f"daily_settle ok: actuals_ok={actuals.get('ok', 0)} "
        f"settled={summary.get('settled_positions', 0)} "
        f"open={summary.get('open_positions', 0)} "
        f"realized_pnl=${summary.get('realized_pnl_usdc', 0.0):.2f} "
        f"elapsed={elapsed:.1f}s"
    )
    update_task(
        "daily_settle",
        {
            "code": 0,
            "outcome": "ok",
            "error": "",
            "actuals_ok": actuals.get("ok", 0),
            "actuals_pending": actuals.get("pending", 0),
            "actuals_error": actuals.get("error", 0),
            "settled_positions": summary.get("settled_positions", 0),
            "open_positions": summary.get("open_positions", 0),
            "realized_pnl_usdc": summary.get("realized_pnl_usdc", 0.0),
            "elapsed_s": round(elapsed, 2),
        },
        path=HEALTH_PATH,
        portfolio={
            "bankroll_usdc": summary.get("bankroll_usdc", 100.0),
            "open_positions": summary.get("open_positions", 0),
            "settled_positions": summary.get("settled_positions", 0),
            "win_rate_pct": summary.get("win_rate_pct"),
            "realized_pnl_usdc": summary.get("realized_pnl_usdc", 0.0),
            "drawdown_triggered": (summary.get("realized_pnl_usdc", 0.0) or 0.0) <= -10.0,
        },
        alert=(
            f"settled {summary.get('settled_positions', 0)} positions, "
            f"realized PnL ${summary.get('realized_pnl_usdc', 0.0):.2f}"
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
