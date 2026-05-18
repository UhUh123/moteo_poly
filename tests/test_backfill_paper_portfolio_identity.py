"""Tests for scripts/backfill_paper_portfolio_identity.py.

The script repairs paper_portfolio.csv rows that landed on disk with
empty target_date / station_id / target_extreme / city / target_unit
/ source_domain columns. Tests pin behaviour we cannot afford to
regress: idempotency, no-overwrite of present values, slug parsing
correctness, atomic write.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "backfill_paper_portfolio_identity",
        ROOT / "scripts" / "backfill_paper_portfolio_identity.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def backfill_mod():
    return _load_module()


# ---- slug parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        (
            "highest-temperature-in-shanghai-on-may-12-2026",
            {"target_date": "2026-05-12", "target_extreme": "max", "city": "Shanghai"},
        ),
        (
            "lowest-temperature-in-buenos-aires-on-may-15-2026",
            {"target_date": "2026-05-15", "target_extreme": "min", "city": "Buenos Aires"},
        ),
        (
            "highest-temperature-in-tel-aviv-on-may-12-2026",
            {"target_date": "2026-05-12", "target_extreme": "max", "city": "Tel Aviv"},
        ),
        (
            "highest-temperature-in-hong-kong-on-may-16-2026-25c",
            {"target_date": "2026-05-16", "target_extreme": "max", "city": "Hong Kong"},
        ),
        (
            "highest-temperature-in-san-francisco-on-october-3-2026",
            {"target_date": "2026-10-03", "target_extreme": "max", "city": "San Francisco"},
        ),
    ],
)
def test_parse_slug_handles_known_shapes(backfill_mod, slug, expected) -> None:
    assert backfill_mod.parse_slug(slug) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-temperature-market",
        "highest-temperature-in-mars-on-fluffuary-99-2026",
        "highest-temperature-in-tokyo-on-may-32-2026",  # invalid day
    ],
)
def test_parse_slug_returns_none_for_garbage(backfill_mod, bad) -> None:
    assert backfill_mod.parse_slug(bad) is None


# ---- archive lookup --------------------------------------------------------


def _write_archived_targets(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["slug", "station_id", "target_unit", "source_domain", "target_date"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in fieldnames})


def test_load_archive_lookup_uses_first_seen(tmp_path: Path, backfill_mod) -> None:
    """Earlier archives win: if the same slug shows up later with a
    different station_id (rotation, manual fix, anything), we keep the
    original station the position was opened against."""
    archive_root = tmp_path / "paper_runs"
    _write_archived_targets(
        archive_root / "20260511T190000Z" / "targets.csv",
        [{"slug": "highest-temperature-in-shanghai-on-may-12-2026",
          "station_id": "ZSPD", "target_unit": "celsius",
          "source_domain": "wunderground.com"}],
    )
    _write_archived_targets(
        archive_root / "20260512T190000Z" / "targets.csv",
        [{"slug": "highest-temperature-in-shanghai-on-may-12-2026",
          "station_id": "ZSSS",  # someone changed it
          "target_unit": "celsius", "source_domain": "wunderground.com"}],
    )

    lookup = backfill_mod.load_archive_lookup(archive_root)
    assert lookup["highest-temperature-in-shanghai-on-may-12-2026"]["station_id"] == "ZSPD"


def test_load_archive_lookup_returns_empty_for_missing_dir(tmp_path: Path, backfill_mod) -> None:
    assert backfill_mod.load_archive_lookup(tmp_path / "nope") == {}


# ---- row repair ------------------------------------------------------------


def test_repair_row_fills_blank_columns_from_slug_and_archive(backfill_mod) -> None:
    archive = {
        "highest-temperature-in-shanghai-on-may-12-2026": {
            "station_id": "ZSPD", "target_unit": "celsius",
            "source_domain": "wunderground.com",
        },
    }
    row = {
        "event_slug": "highest-temperature-in-shanghai-on-may-12-2026",
        "interval_unit": "celsius",
        # All identity cols blank:
        "target_date": "", "station_id": "", "target_extreme": "",
        "city": "", "target_unit": "", "source_domain": "",
    }
    fixes = backfill_mod.repair_row(row, archive)
    assert fixes == {
        "target_date": "2026-05-12",
        "target_extreme": "max",
        "city": "Shanghai",
        "station_id": "ZSPD",
        "target_unit": "celsius",
        "source_domain": "wunderground.com",
    }


def test_repair_row_does_not_overwrite_present_values(backfill_mod) -> None:
    """Idempotency: a second run on already-repaired data must be a no-op.
    More importantly, if a human or future writer already filled some
    cells correctly, the script must leave them alone."""
    archive = {
        "highest-temperature-in-tokyo-on-may-12-2026": {
            "station_id": "RJTT", "target_unit": "celsius",
            "source_domain": "wunderground.com",
        },
    }
    row = {
        "event_slug": "highest-temperature-in-tokyo-on-may-12-2026",
        "target_date": "2026-05-12", "station_id": "RJAA",  # different on purpose
        "target_extreme": "max", "city": "Tokyo",
        "target_unit": "celsius", "source_domain": "wunderground.com",
    }
    fixes = backfill_mod.repair_row(row, archive)
    assert fixes == {}, "fully populated row should require no fixes"


def test_repair_row_falls_back_to_heuristic_source_domain_for_hko(backfill_mod) -> None:
    """HKO is the one non-wunderground.com station we use today."""
    archive = {}  # archive miss
    row = {
        "event_slug": "highest-temperature-in-hong-kong-on-may-16-2026",
        "target_date": "2026-05-16", "station_id": "HKO",
        "target_extreme": "max", "city": "Hong Kong",
        "target_unit": "celsius", "source_domain": "",
    }
    fixes = backfill_mod.repair_row(row, archive)
    assert fixes == {"source_domain": "weather.gov.hk"}


def test_repair_row_uses_wunderground_default_for_icao_stations(backfill_mod) -> None:
    archive = {}
    row = {
        "event_slug": "highest-temperature-in-tokyo-on-may-12-2026",
        "target_date": "2026-05-12", "station_id": "RJTT",
        "target_extreme": "max", "city": "Tokyo",
        "target_unit": "celsius", "source_domain": "",
    }
    fixes = backfill_mod.repair_row(row, archive)
    assert fixes == {"source_domain": "wunderground.com"}


def test_repair_row_handles_unknown_slug_gracefully(backfill_mod) -> None:
    """A row whose event_slug isn't a temperature market: we can't
    parse it, the archive can't look it up — the script must return
    no fixes rather than crash."""
    row = {
        "event_slug": "what-is-this-even",
        "target_date": "", "station_id": "", "target_extreme": "",
        "city": "", "target_unit": "", "source_domain": "",
    }
    assert backfill_mod.repair_row(row, archive={}) == {}


# ---- end-to-end via main() -------------------------------------------------


def _write_portfolio(path: Path, rows: list[dict], extra_cols: list[str] | None = None) -> None:
    base_cols = ["event_slug", "status", "side", "stake_usdc"]
    cols = base_cols + (extra_cols or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in cols})


def test_main_dry_run_does_not_write(tmp_path: Path, backfill_mod, capsys) -> None:
    portfolio = tmp_path / "paper_portfolio.csv"
    _write_portfolio(portfolio, [{
        "event_slug": "highest-temperature-in-shanghai-on-may-12-2026",
        "status": "won", "side": "BUY_NO", "stake_usdc": "0.25",
    }])

    archive_root = tmp_path / "paper_runs"
    _write_archived_targets(
        archive_root / "20260511T190000Z" / "targets.csv",
        [{"slug": "highest-temperature-in-shanghai-on-may-12-2026",
          "station_id": "ZSPD", "target_unit": "celsius",
          "source_domain": "wunderground.com"}],
    )

    rc = backfill_mod.main([
        "--portfolio", str(portfolio),
        "--archive-root", str(archive_root),
    ])
    assert rc == 0

    rows_after = list(csv.DictReader(portfolio.open(newline="", encoding="utf-8")))
    # Dry-run: original file untouched, no new columns added on disk.
    assert "target_date" not in rows_after[0]
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_apply_repairs_and_creates_backup(tmp_path: Path, backfill_mod) -> None:
    portfolio = tmp_path / "paper_portfolio.csv"
    _write_portfolio(portfolio, [
        {"event_slug": "highest-temperature-in-shanghai-on-may-12-2026",
         "status": "won", "side": "BUY_NO", "stake_usdc": "0.25"},
        {"event_slug": "highest-temperature-in-tokyo-on-may-11-2026",
         "status": "lost", "side": "BUY_YES", "stake_usdc": "0.25"},
    ])

    archive_root = tmp_path / "paper_runs"
    _write_archived_targets(
        archive_root / "20260510T190000Z" / "targets.csv",
        [
            {"slug": "highest-temperature-in-shanghai-on-may-12-2026",
             "station_id": "ZSPD", "target_unit": "celsius",
             "source_domain": "wunderground.com"},
            {"slug": "highest-temperature-in-tokyo-on-may-11-2026",
             "station_id": "RJTT", "target_unit": "celsius",
             "source_domain": "wunderground.com"},
        ],
    )

    rc = backfill_mod.main([
        "--portfolio", str(portfolio),
        "--archive-root", str(archive_root),
        "--apply",
    ])
    assert rc == 0

    rows_after = list(csv.DictReader(portfolio.open(newline="", encoding="utf-8")))
    by_slug = {r["event_slug"]: r for r in rows_after}
    sh = by_slug["highest-temperature-in-shanghai-on-may-12-2026"]
    assert sh["target_date"] == "2026-05-12"
    assert sh["station_id"] == "ZSPD"
    assert sh["target_extreme"] == "max"
    assert sh["city"] == "Shanghai"
    assert sh["target_unit"] == "celsius"
    assert sh["source_domain"] == "wunderground.com"
    tk = by_slug["highest-temperature-in-tokyo-on-may-11-2026"]
    assert tk["station_id"] == "RJTT"
    assert tk["city"] == "Tokyo"

    # Backup with .bak.YYYYMMDD timestamp prefix exists
    backups = list(portfolio.parent.glob(portfolio.name + ".bak.*"))
    assert len(backups) == 1


def test_main_apply_is_idempotent(tmp_path: Path, backfill_mod) -> None:
    """Running --apply twice must not change the file's content the
    second time and must not add a second copy of the columns."""
    portfolio = tmp_path / "paper_portfolio.csv"
    _write_portfolio(portfolio, [{
        "event_slug": "highest-temperature-in-shanghai-on-may-12-2026",
        "status": "won", "side": "BUY_NO", "stake_usdc": "0.25",
    }])
    archive_root = tmp_path / "paper_runs"
    _write_archived_targets(
        archive_root / "20260511T190000Z" / "targets.csv",
        [{"slug": "highest-temperature-in-shanghai-on-may-12-2026",
          "station_id": "ZSPD", "target_unit": "celsius",
          "source_domain": "wunderground.com"}],
    )

    backfill_mod.main([
        "--portfolio", str(portfolio),
        "--archive-root", str(archive_root),
        "--apply",
    ])
    first_text = portfolio.read_text(encoding="utf-8")
    first_cols = list(csv.DictReader(portfolio.open(newline="", encoding="utf-8")).fieldnames or [])

    backfill_mod.main([
        "--portfolio", str(portfolio),
        "--archive-root", str(archive_root),
        "--apply",
    ])
    second_text = portfolio.read_text(encoding="utf-8")
    second_cols = list(csv.DictReader(portfolio.open(newline="", encoding="utf-8")).fieldnames or [])

    assert first_text == second_text, "second run must produce identical bytes"
    assert first_cols == second_cols, "column order/identity must be stable"


def test_main_returns_2_for_missing_portfolio(tmp_path: Path, backfill_mod) -> None:
    rc = backfill_mod.main([
        "--portfolio", str(tmp_path / "does_not_exist.csv"),
        "--apply",
    ])
    assert rc == 2


def test_merge_current_targets_fills_open_positions_not_in_archive(
    tmp_path: Path, backfill_mod
) -> None:
    """Open paper positions for FUTURE dates aren't in any archive yet
    (paper_runs/<run> snapshots are only created at the moment of a
    pre-open settle, and the position lives forward of every existing
    run). Their slug IS in the active data/targets.csv. Backfill must
    use it as a lower-priority fallback so these positions get an
    accurate station_id, target_unit, and source_domain."""
    portfolio = tmp_path / "paper_portfolio.csv"
    _write_portfolio(portfolio, [
        {"event_slug": "highest-temperature-in-tokyo-on-may-12-2026",
         "status": "won", "side": "BUY_YES", "stake_usdc": "0.25"},
        {"event_slug": "highest-temperature-in-miami-on-may-18-2026",
         "status": "open", "side": "BUY_NO", "stake_usdc": "0.25"},
    ])

    archive_root = tmp_path / "paper_runs"
    _write_archived_targets(
        archive_root / "20260511T190000Z" / "targets.csv",
        [{"slug": "highest-temperature-in-tokyo-on-may-12-2026",
          "station_id": "RJTT", "target_unit": "celsius",
          "source_domain": "wunderground.com"}],
    )

    current_targets = tmp_path / "data" / "targets.csv"
    _write_archived_targets(
        current_targets,
        [{"slug": "highest-temperature-in-miami-on-may-18-2026",
          "station_id": "KMIA", "target_unit": "fahrenheit",
          "source_domain": "wunderground.com"}],
    )

    rc = backfill_mod.main([
        "--portfolio", str(portfolio),
        "--archive-root", str(archive_root),
        "--current-targets", str(current_targets),
        "--apply",
    ])
    assert rc == 0

    rows = list(csv.DictReader(portfolio.open(newline="", encoding="utf-8")))
    by_slug = {r["event_slug"]: r for r in rows}
    assert by_slug["highest-temperature-in-tokyo-on-may-12-2026"]["station_id"] == "RJTT"
    miami = by_slug["highest-temperature-in-miami-on-may-18-2026"]
    assert miami["station_id"] == "KMIA"
    assert miami["target_unit"] == "fahrenheit"
    assert miami["source_domain"] == "wunderground.com"


def test_archive_takes_priority_over_current_targets(tmp_path: Path, backfill_mod) -> None:
    """If a slug appears in both the archive and the current targets.csv
    with different station_ids, the archive wins. Running the backfill
    must not silently rewrite the historic record from a fresh
    targets.csv that may have rotated stations.
    """
    portfolio = tmp_path / "paper_portfolio.csv"
    _write_portfolio(portfolio, [{
        "event_slug": "highest-temperature-in-paris-on-may-12-2026",
        "status": "won", "side": "BUY_NO", "stake_usdc": "0.25",
    }])

    archive_root = tmp_path / "paper_runs"
    _write_archived_targets(
        archive_root / "20260511T190000Z" / "targets.csv",
        [{"slug": "highest-temperature-in-paris-on-may-12-2026",
          "station_id": "LFPB", "target_unit": "celsius",
          "source_domain": "wunderground.com"}],
    )

    current_targets = tmp_path / "data" / "targets.csv"
    _write_archived_targets(
        current_targets,
        [{"slug": "highest-temperature-in-paris-on-may-12-2026",
          "station_id": "LFPG",  # different from archive
          "target_unit": "celsius",
          "source_domain": "wunderground.com"}],
    )

    backfill_mod.main([
        "--portfolio", str(portfolio),
        "--archive-root", str(archive_root),
        "--current-targets", str(current_targets),
        "--apply",
    ])
    rows = list(csv.DictReader(portfolio.open(newline="", encoding="utf-8")))
    assert rows[0]["station_id"] == "LFPB", "archive must beat current targets"
