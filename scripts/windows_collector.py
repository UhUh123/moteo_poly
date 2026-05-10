"""Windows data collector for Polymarket weather markets.

Runs as a scheduled task. Two modes:

  regular: scan weather events + fetch CLOB orderbooks + snapshot to history.
           Intended cadence: every 5 minutes.

  hot:     skip the full scan, refetch orderbooks ONLY for markets closing
           within the next `--hot-window-min` minutes (default 60). Intended
           cadence: every 1 minute.

The collector never opens or settles paper trades. It only collects raw data
so that later analysis can reconstruct how prices moved close to resolution.
All outputs land under C:\\poly\\detect-temperature by default; history folders
preserve the full snapshot so that overwrites in `data/` do not destroy it.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from detect_temperature.pipeline import (
    fetch_clob_orderbooks,
    scan_polymarket_weather,
)
from detect_temperature.polymarket import (
    PolymarketClobClient,
    token_ids_from_market_records,
    write_json,
)
from detect_temperature.status import update_task


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
HISTORY_DIR = DATA_DIR / "history"
HEALTH_PATH = ROOT / "status" / "health.json"

REGULAR_SNAPSHOT_FILES = (
    "polymarket_weather_markets.csv",
    "polymarket_weather_events.json",
    "polymarket_geoblock.json",
    "polymarket_orderbooks.json",
)


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _configure_logging() -> logging.Logger:
    _ensure_dirs()
    log_path = LOG_DIR / "collector.log"
    logger = logging.getLogger("windows_collector")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _snapshot_dir(mode: str, now: datetime) -> Path:
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%M%S")
    path = HISTORY_DIR / day / f"{stamp}-{mode}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _archive_regular(snapshot_dir: Path) -> list[str]:
    copied: list[str] = []
    for name in REGULAR_SNAPSHOT_FILES:
        src = DATA_DIR / name
        if not src.exists():
            continue
        shutil.copy2(src, snapshot_dir / name)
        copied.append(name)
    return copied


def _active_close_watch(markets_csv: Path, window_min: int) -> list[dict]:
    if not markets_csv.exists():
        return []
    now = datetime.now(timezone.utc)
    soon = now + timedelta(minutes=window_min)
    rows: list[dict] = []
    with markets_csv.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            end_date = (row.get("end_date") or "").replace("Z", "+00:00")
            if not end_date:
                continue
            try:
                when = datetime.fromisoformat(end_date)
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            accepting = (row.get("accepting_orders") or "").strip()
            active = (row.get("active") or "").strip()
            closed = (row.get("closed") or "").strip()
            if closed == "1":
                continue
            if accepting and accepting != "1":
                continue
            if active and active != "1":
                continue
            if now <= when <= soon:
                rows.append(row)
    return rows


def do_regular(logger: logging.Logger, now: datetime) -> int:
    snapshot_dir = _snapshot_dir("regular", now)
    logger.info(f"regular scan begin -> history={snapshot_dir}")
    try:
        scan_rows = scan_polymarket_weather(
            output_path=DATA_DIR / "polymarket_weather_markets.csv",
            raw_output_path=DATA_DIR / "polymarket_weather_events.json",
            geoblock_output_path=DATA_DIR / "polymarket_geoblock.json",
        )
    except Exception as exc:
        logger.error(f"scan failed: {exc}")
        update_task("collector_regular", {"code": 2, "error": str(exc)}, path=HEALTH_PATH)
        return 2
    active = sum(1 for r in scan_rows if str(r.get("active")) in {"1", "True", "true"})
    logger.info(f"scan ok: {len(scan_rows)} markets, active={active}")

    orderbook_error: str | None = None
    try:
        fetch_clob_orderbooks(
            markets_path=DATA_DIR / "polymarket_weather_markets.csv",
            output_path=DATA_DIR / "polymarket_orderbooks.json",
        )
    except Exception as exc:
        orderbook_error = str(exc)
        logger.error(f"orderbook fetch failed: {exc}")

    copied = _archive_regular(snapshot_dir)
    logger.info(f"regular snapshot stored: {len(copied)} files")
    update_task(
        "collector_regular",
        {
            "code": 0,
            "markets_scanned": len(scan_rows),
            "active_markets": active,
            "snapshot_dir": str(snapshot_dir),
            "orderbook_error": orderbook_error or "",
        },
        path=HEALTH_PATH,
    )
    return 0


def do_hot(logger: logging.Logger, now: datetime, window_min: int) -> int:
    markets_csv = DATA_DIR / "polymarket_weather_markets.csv"
    watch = _active_close_watch(markets_csv, window_min=window_min)
    if not watch:
        logger.info(f"hot skip: no markets closing within {window_min} min")
        update_task(
            "collector_hot",
            {"code": 0, "markets_watched": 0, "outcome": "skip_no_closing"},
            path=HEALTH_PATH,
        )
        return 0

    records_for_tokens = [
        {"yes_token_id": row.get("yes_token_id"), "no_token_id": row.get("no_token_id")}
        for row in watch
    ]
    token_ids = token_ids_from_market_records(records_for_tokens, include_no=True)
    if not token_ids:
        logger.info("hot skip: no token ids on closing markets")
        update_task(
            "collector_hot",
            {"code": 0, "markets_watched": len(watch), "outcome": "skip_no_tokens"},
            path=HEALTH_PATH,
        )
        return 0

    logger.info(f"hot refresh: {len(watch)} markets / {len(token_ids)} token_ids")
    client = PolymarketClobClient()
    try:
        books = client.fetch_order_books(token_ids)
    except Exception as exc:
        logger.error(f"hot orderbook fetch failed: {exc}")
        update_task(
            "collector_hot",
            {"code": 2, "markets_watched": len(watch), "error": str(exc)},
            path=HEALTH_PATH,
        )
        return 2

    snapshot_dir = _snapshot_dir("hot", now)
    payload = {
        "source_markets_path": str(markets_csv),
        "window_minutes": window_min,
        "requested_token_ids": len(token_ids),
        "market_slugs": [row.get("market_slug") for row in watch],
        "books": books,
    }
    write_json(payload, snapshot_dir / "polymarket_orderbooks.json")
    logger.info(f"hot snapshot stored: {snapshot_dir}")
    update_task(
        "collector_hot",
        {
            "code": 0,
            "markets_watched": len(watch),
            "token_ids_requested": len(token_ids),
            "snapshot_dir": str(snapshot_dir),
            "outcome": "snapshot",
        },
        path=HEALTH_PATH,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="windows_collector")
    parser.add_argument("--mode", choices=("regular", "hot"), required=True)
    parser.add_argument("--hot-window-min", type=int, default=60)
    args = parser.parse_args(argv)

    logger = _configure_logging()
    now = datetime.now(timezone.utc)
    started = time.time()
    logger.info(f"collector start mode={args.mode} now_utc={now.isoformat(timespec='seconds')}")

    if args.mode == "regular":
        code = do_regular(logger, now)
    else:
        code = do_hot(logger, now, window_min=args.hot_window_min)

    elapsed = time.time() - started
    logger.info(f"collector end code={code} elapsed={elapsed:.2f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
