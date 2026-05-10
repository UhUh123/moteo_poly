"""Single source of truth for the automated pipeline's health.

Every Windows scheduled task (collector, near-close refresh, daily open,
daily settle, weekly calibration) updates one JSON file here. That file is
what `docs/ORCHESTRATION.md` points at, and what any future AI agent should
read first when asked "what's the system doing right now?".

The file is small (<10 KB), so we rewrite it atomically on every update:
  1. Acquire a lock file (portable cross-platform fcntl / msvcrt wrapper).
  2. Read current JSON (or start from a scaffold if missing).
  3. Deep-merge the incoming per-task dict into `tasks[task_name]`.
  4. Update top-level `updated_at`.
  5. Write to `<path>.tmp` and os.replace.

The lock file approach is good enough for our scale — the collector fires
at most twice per minute (regular + hot) and all other tasks are daily, so
contention is minimal.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KNOWN_TASKS = (
    "collector_regular",
    "collector_hot",
    "daily_open_trades",
    "near_close_refresh",
    "daily_settle",
    "calibration_refresh",
)

DEFAULT_HEALTH_PATH = Path("status") / "health.json"
_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT_S = 5.0
_ALERT_KEEP = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _scaffold() -> dict[str, Any]:
    return {
        "updated_at": _now_iso(),
        "tasks": {name: {} for name in KNOWN_TASKS},
        "portfolio": {},
        "alerts": [],
    }


@contextmanager
def _file_lock(lock_path: Path):
    """Tiny cross-platform file lock via exclusive-create.

    Good enough for two writers per minute. Stale locks older than 30s are
    forcibly removed — prevents permanent deadlock if a writer crashes.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            # Break stale locks
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 30:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"lock acquisition timed out: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def load_health(path: str | Path = DEFAULT_HEALTH_PATH) -> dict[str, Any]:
    """Return the current health JSON or a scaffold when the file is missing."""
    p = Path(path)
    if not p.exists():
        return _scaffold()
    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return _scaffold()
    # Backfill missing top-level keys to keep callers simple
    scaffold = _scaffold()
    for key, value in scaffold.items():
        payload.setdefault(key, value)
    return payload


def update_task(
    task_name: str,
    fields: dict[str, Any],
    path: str | Path = DEFAULT_HEALTH_PATH,
    alert: str | None = None,
    portfolio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge `fields` into `tasks[task_name]` and rewrite the file atomically.

    `fields` always gets a `last_run` timestamp added if not provided.
    `alert`, if given, is prepended to `alerts[]` and the list is trimmed.
    `portfolio`, if given, replaces `portfolio` wholesale (caller produces it
    from `summarize_portfolio` or similar).
    """
    p = Path(path)
    lock_path = p.with_suffix(p.suffix + _LOCK_SUFFIX)

    fields = dict(fields)
    fields.setdefault("last_run", _now_iso())

    with _file_lock(lock_path):
        payload = load_health(p)
        task_section = payload["tasks"].setdefault(task_name, {})
        task_section.update(fields)
        payload["updated_at"] = _now_iso()
        if portfolio is not None:
            payload["portfolio"] = portfolio
        if alert:
            alerts = payload.setdefault("alerts", [])
            alerts.insert(0, f"{_now_iso()} {task_name}: {alert}")
            del alerts[_ALERT_KEEP:]

        tmp_path = p.with_suffix(p.suffix + ".tmp")
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_path, p)
    return payload
