"""Daily automated open-trades cycle.

Runs the full paper pipeline exactly like the dashboard "Открыть сделки"
button but from Task Scheduler:

    scan-polymarket-weather
  -> build-polymarket-targets
  -> build-features (with Open-Meteo)
  -> predict-gbm (with station bias)
  -> build-market-signals (bankroll_100)
  -> fetch-clob-orderbooks
  -> run-strategy-lab (bankroll_100)
  -> open-strategy-paper-trades (bankroll_100)

Drawdown guard is already wired inside run_open_trades_pipeline — if the
already-realized PnL has breached the -$10 floor, no new positions are
opened and the script exits with code 3. In that case health.json gets a
loud alert so any human (or AI) checking next morning sees immediately.

Designed to be idempotent: running it twice in the same day just archives
the previous open state and opens fresh positions based on the latest
scan. Exit codes match the rest of the CLI:
    0 - success
    2 - pipeline error (network, API failure)
    3 - drawdown kill-switch tripped; no positions opened
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "daily_open_trades.log"
HEALTH_PATH = ROOT / "status" / "health.json"


def _configure_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("daily_open_trades")
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
    # Local imports so any setup failure still writes to the log first
    logger = _configure_logging()
    started = time.time()
    logger.info("daily_open_trades start")

    from detect_temperature.paper_server import run_open_trades_pipeline
    from detect_temperature.risk_guards import DrawdownAbort
    from detect_temperature.status import update_task

    try:
        result = run_open_trades_pipeline(
            project_root=ROOT,
            risk_profile="bankroll_100",
            bankroll_usdc=100.0,
            finalization_lag_days=1,
        )
    except DrawdownAbort as exc:
        logger.error(f"drawdown kill-switch: {exc}")
        update_task(
            "daily_open_trades",
            {"code": 3, "outcome": "drawdown_blocked", "error": str(exc)},
            path=HEALTH_PATH,
            alert=f"BLOCKED: {exc}",
        )
        return 3
    except Exception as exc:
        logger.exception(f"daily_open_trades failed: {exc}")
        update_task(
            "daily_open_trades",
            {"code": 2, "outcome": "error", "error": str(exc)},
            path=HEALTH_PATH,
            alert=f"ERROR: {exc}",
        )
        return 2

    paper = result.get("paper_summary", {}) or {}
    lab = (result.get("market_pipeline") or {}).get("strategy_lab", {})
    positions = int(paper.get("positions", 0))
    staked = float(paper.get("total_staked_usdc", 0.0) or 0.0)
    candidates = int(lab.get("trade_candidates", 0))
    robust = int(lab.get("robust_pass", 0))
    selected = int(lab.get("selected_positions", 0))

    elapsed = time.time() - started
    logger.info(
        f"daily_open_trades ok: candidates={candidates} robust={robust} selected={selected} "
        f"positions={positions} staked=${staked:.2f} elapsed={elapsed:.1f}s"
    )
    update_task(
        "daily_open_trades",
        {
            "code": 0,
            "outcome": "opened" if positions else "no_positions",
            "error": "",
            "candidates": candidates,
            "robust": robust,
            "selected": selected,
            "positions_opened": positions,
            "total_staked_usdc": round(staked, 4),
            "elapsed_s": round(elapsed, 2),
            "archive_dir": result.get("archive_dir"),
        },
        path=HEALTH_PATH,
        portfolio={
            "bankroll_usdc": paper.get("bankroll_usdc", 100.0),
            "open_positions": paper.get("open_positions", 0),
            "settled_positions": paper.get("settled_positions", 0),
            "win_rate_pct": paper.get("win_rate_pct"),
            "realized_pnl_usdc": paper.get("realized_pnl_usdc", 0.0),
            "drawdown_triggered": False,
        },
        alert=(
            f"opened {positions} positions, staked ${staked:.2f}"
            if positions
            else "no positions opened (no robust candidates)"
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
