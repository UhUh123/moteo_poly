from __future__ import annotations

import json
from pathlib import Path

from detect_temperature.state_archive import (
    DEFAULT_TRACKED,
    MANIFEST_NAME,
    archive_model_state,
    resolve_state_file,
)


def _set_up_project(tmp_path: Path, *, predictions: str, signals: str) -> Path:
    """Build a minimal project layout with predictions + signals files,
    return the project_root."""
    root = tmp_path / "project"
    (root / "artifacts").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "artifacts" / "predictions_gbm.csv").write_text(predictions, encoding="utf-8")
    (root / "artifacts" / "market_signals.csv").write_text(signals, encoding="utf-8")
    return root


def test_archive_creates_pool_and_manifest(tmp_path: Path) -> None:
    root = _set_up_project(tmp_path, predictions="slug,p\nfoo,0.5\n", signals="slug,edge\nfoo,0.1\n")
    snap = tmp_path / "snap1"

    manifest = archive_model_state(snap, root)

    # Manifest is written and contains both logical names
    assert (snap / MANIFEST_NAME).exists()
    on_disk = json.loads((snap / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(on_disk.keys()) == {"predictions_gbm", "market_signals"}
    assert manifest == on_disk

    # SHAs are hex strings of length 12
    for sha in manifest.values():
        assert len(sha) == 12
        int(sha, 16)  # raises if not hex

    # Files actually live in the pool
    pred_path = root / "data" / "history" / "_state" / manifest["predictions_gbm"] / "predictions_gbm.csv"
    sig_path = root / "data" / "history" / "_state" / manifest["market_signals"] / "market_signals.csv"
    assert pred_path.read_text(encoding="utf-8") == "slug,p\nfoo,0.5\n"
    assert sig_path.read_text(encoding="utf-8") == "slug,edge\nfoo,0.1\n"


def test_archive_dedupes_identical_content(tmp_path: Path) -> None:
    """Two snapshots taken back-to-back with no model change must NOT
    duplicate the file in the pool — that is the entire point of
    content-addressing."""
    root = _set_up_project(tmp_path, predictions="slug,p\nfoo,0.5\n", signals="slug,edge\nfoo,0.1\n")

    m1 = archive_model_state(tmp_path / "snap_a", root)
    m2 = archive_model_state(tmp_path / "snap_b", root)

    assert m1 == m2  # same SHA on both calls
    pool_root = root / "data" / "history" / "_state"
    pool_entries = list(pool_root.rglob("*.csv"))
    # exactly one copy of each tracked file in the pool
    assert len(pool_entries) == 2


def test_archive_creates_new_pool_entry_when_content_changes(tmp_path: Path) -> None:
    root = _set_up_project(tmp_path, predictions="slug,p\nfoo,0.5\n", signals="slug,edge\nfoo,0.1\n")

    m1 = archive_model_state(tmp_path / "snap_a", root)

    # Model retrains: predictions change, signals don't
    (root / "artifacts" / "predictions_gbm.csv").write_text("slug,p\nfoo,0.42\n", encoding="utf-8")
    m2 = archive_model_state(tmp_path / "snap_b", root)

    assert m1["predictions_gbm"] != m2["predictions_gbm"], "new predictions content must get a new SHA"
    assert m1["market_signals"] == m2["market_signals"], "unchanged signals must reuse SHA"

    # Pool now has 3 files: 2 versions of predictions + 1 of signals
    pool_root = root / "data" / "history" / "_state"
    pool_entries = list(pool_root.rglob("*.csv"))
    assert len(pool_entries) == 3


def test_archive_silently_skips_missing_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "artifacts").mkdir(parents=True)
    (root / "data").mkdir()
    # Only predictions exists; market_signals is missing
    (root / "artifacts" / "predictions_gbm.csv").write_text("slug,p\nfoo,0.5\n", encoding="utf-8")

    manifest = archive_model_state(tmp_path / "snap", root)

    assert "predictions_gbm" in manifest
    assert "market_signals" not in manifest


def test_resolve_state_file_round_trip(tmp_path: Path) -> None:
    root = _set_up_project(tmp_path, predictions="slug,p\nfoo,0.5\n", signals="slug,edge\nfoo,0.1\n")
    snap = tmp_path / "snap"
    archive_model_state(snap, root)

    resolved = resolve_state_file(snap, "predictions_gbm", root)
    assert resolved is not None
    assert resolved.read_text(encoding="utf-8") == "slug,p\nfoo,0.5\n"


def test_resolve_state_file_returns_none_when_no_manifest(tmp_path: Path) -> None:
    root = _set_up_project(tmp_path, predictions="slug,p\nfoo,0.5\n", signals="slug,edge\nfoo,0.1\n")
    snap = tmp_path / "snap_without_manifest"
    snap.mkdir()
    # No archive_model_state call here

    assert resolve_state_file(snap, "predictions_gbm", root) is None


def test_default_tracked_covers_critical_files() -> None:
    """Guard against accidentally dropping a file from the tracked set
    in a future refactor — the audit found these three files were
    silently lost on every model rebuild before this fix shipped."""
    assert "predictions_gbm" in DEFAULT_TRACKED
    assert "market_signals" in DEFAULT_TRACKED
    assert "station_calibration" in DEFAULT_TRACKED
