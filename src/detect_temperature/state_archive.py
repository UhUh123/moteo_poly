"""Content-addressed archive for model state files.

Why this exists
---------------
Every 5 minutes the Windows collector saves a snapshot of the live
Polymarket markets and orderbooks. This is enough to replay what
*the market* looked like at any past minute. But it is NOT enough to
replay what *our model* thought was a fair price at that minute, which
is what a walk-forward backtest (chapter 5 §7.1) actually needs.

Naive fix: copy artifacts/predictions_gbm.csv and
artifacts/market_signals.csv into every snapshot directory. At ~640 KB
per pair and 288 snapshots/day that is ~185 MB/day of mostly identical
copies, because those files only update once per day (after
daily_open_trades) and once per week (calibration_refresh).

Better fix (this module): a content-addressed pool. Each file goes
into `data/history/_state/<sha8>/<filename>` exactly once per unique
content. Each snapshot dir gets a small `state_manifest.json` that
maps logical name -> SHA. Replaying a past snapshot becomes
"read manifest, follow pointer".

Disk impact in practice: ~640 KB on the days predictions or signals
actually change, ~0 KB on every other snapshot. About 200x cheaper
than naive copying with no information loss.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Iterable, Mapping

# Logical name -> source path relative to project root. Logical names
# are what the manifest stores so the archive layout doesn't change
# even if we move files around inside artifacts/ later.
DEFAULT_TRACKED: Mapping[str, str] = {
    "predictions_gbm": "artifacts/predictions_gbm.csv",
    "market_signals":  "artifacts/market_signals.csv",
    "station_calibration": "data/station_calibration.csv",
}

MANIFEST_NAME = "state_manifest.json"


def archive_model_state(
    snapshot_dir: Path,
    project_root: Path,
    tracked: Mapping[str, str] | None = None,
    state_pool_dir: Path | None = None,
) -> dict[str, str]:
    """Persist current model-state files into the content-addressed pool
    and write a manifest into `snapshot_dir`.

    Returns the manifest dict (logical_name -> sha) so callers can log
    counts. Files that don't exist are silently skipped — tests and
    early bootstrap runs may not have all artifacts yet.
    """
    if tracked is None:
        tracked = DEFAULT_TRACKED
    if state_pool_dir is None:
        state_pool_dir = project_root / "data" / "history" / "_state"

    manifest: dict[str, str] = {}
    for logical, rel_path in tracked.items():
        src = project_root / rel_path
        if not src.exists():
            continue
        sha8 = _short_sha(src)
        pool_entry = state_pool_dir / sha8 / Path(rel_path).name
        if not pool_entry.exists():
            pool_entry.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, pool_entry)
        manifest[logical] = sha8

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def resolve_state_file(
    snapshot_dir: Path,
    logical_name: str,
    project_root: Path,
    state_pool_dir: Path | None = None,
) -> Path | None:
    """Reverse the manifest: find the actual file in the pool that was
    active at the time of the given snapshot. Returns None if the
    snapshot has no manifest, no entry for `logical_name`, or the
    pooled file is missing.

    Use this from analysis scripts and walk-forward backtests:
        path = resolve_state_file(snap_dir, "predictions_gbm", root)
        df = pd.read_csv(path)
    """
    if state_pool_dir is None:
        state_pool_dir = project_root / "data" / "history" / "_state"
    manifest_path = snapshot_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sha = manifest.get(logical_name)
    if not sha:
        return None
    rel = DEFAULT_TRACKED.get(logical_name)
    if rel is None:
        return None
    candidate = state_pool_dir / sha / Path(rel).name
    return candidate if candidate.exists() else None


def _short_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]
