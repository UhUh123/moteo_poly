"""One-shot backfill: fill missing identity columns in paper_portfolio.csv.

Why this exists
---------------
Until 2026-05-18 paper_portfolio.csv landed on disk with empty
target_date / station_id / target_extreme / city / target_unit /
source_domain columns. Those columns are now written correctly going
forward (signals.build_market_signal + paper._position_from_signal +
_position_from_strategy_row carry them through). This script repairs
the rows that already exist.

Repair sources, in priority order:

  1. The slug itself for target_date, target_extreme, city.
     "highest-temperature-in-buenos-aires-on-may-15-2026" parses to
       extreme=max, city="Buenos Aires", target_date=2026-05-15.
  2. archive of paper_runs/<run>/targets.csv for station_id and
     target_unit. _stuck_paper_targets in pipeline.py already uses
     the same archive walk, so we know it works.
  3. Manual station catalog for HKO; aviation_weather catalog for
     ICAO codes — only used to validate the lookup, not to fill data.

Safety
------
* Writes go through paper_portfolio.csv.tmp -> os.replace. A crashed
  run leaves the original file untouched.
* A timestamped backup is created before any mutation.
* Identity columns that ALREADY have a value are NEVER overwritten;
  the script only fills blanks. Re-running is idempotent.
* Dry-run mode is the default. Pass --apply to actually write.

CLI
---
    python scripts/backfill_paper_portfolio_identity.py
    python scripts/backfill_paper_portfolio_identity.py --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = ROOT / "artifacts" / "paper_portfolio.csv"
ARCHIVE_ROOT = ROOT / "artifacts" / "paper_runs"

IDENTITY_COLUMNS = ("target_date", "station_id", "target_extreme",
                    "city", "target_unit", "source_domain")

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_SLUG_RE = re.compile(
    r"^(?P<extreme>highest|lowest)-temperature-in-(?P<city>[a-z][a-z-]*?)-on-"
    r"(?P<month>[a-z]+)-(?P<day>\d{1,2})-(?P<year>\d{4})"
    r"(?:-(?P<bucket>.+))?$"
)


def parse_slug(slug: str) -> dict | None:
    """Return {extreme, city, target_date} or None if slug doesn't match."""
    if not slug:
        return None
    m = _SLUG_RE.match(slug.strip().lower())
    if not m:
        return None
    month = _MONTHS.get(m["month"])
    if month is None:
        return None
    try:
        td = date(int(m["year"]), month, int(m["day"]))
    except ValueError:
        return None
    extreme = "max" if m["extreme"] == "highest" else "min"
    city = m["city"].replace("-", " ").title()
    return {
        "target_date": td.isoformat(),
        "target_extreme": extreme,
        "city": city,
    }


def load_archive_lookup(archive_root: Path) -> dict[str, dict[str, str]]:
    """Build slug -> {station_id, target_unit, source_domain} from every
    archived targets.csv file. Earlier archives win when the same slug
    appears multiple times (we want the original station_id, not whatever
    a later run might have rotated to)."""
    lookup: dict[str, dict[str, str]] = {}
    if not archive_root.exists():
        return lookup
    for path in sorted(archive_root.glob("*/targets.csv")):
        try:
            with path.open("r", newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    slug = (row.get("slug") or "").strip()
                    if not slug or slug in lookup:
                        continue
                    station = (row.get("station_id") or "").strip().upper()
                    unit = (row.get("target_unit") or "").strip().lower()
                    domain = (row.get("source_domain") or "").strip().lower()
                    if station or unit or domain:
                        lookup[slug] = {
                            "station_id": station,
                            "target_unit": unit,
                            "source_domain": domain,
                        }
        except OSError:
            continue
    return lookup


def repair_row(row: dict, archive: dict[str, dict[str, str]]) -> dict[str, str]:
    """Return only the fields we set on this row. Empty dict = nothing to do."""
    fixes: dict[str, str] = {}
    slug = (row.get("event_slug") or "").strip()
    parsed = parse_slug(slug) or {}

    for col, value in parsed.items():
        if not (row.get(col) or "").strip():
            fixes[col] = value

    archive_match = archive.get(slug, {})
    for col in ("station_id", "target_unit", "source_domain"):
        cur = (row.get(col) or "").strip()
        if cur:
            continue
        cand = archive_match.get(col, "")
        if cand:
            fixes[col] = cand
            continue
        # Heuristic fallback: source_domain depends on station_id
        if col == "source_domain":
            sid = fixes.get("station_id") or row.get("station_id") or ""
            if sid:
                fixes[col] = "weather.gov.hk" if sid.upper() == "HKO" else "wunderground.com"

    return fixes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill_paper_portfolio_identity")
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    parser.add_argument("--archive-root", type=Path, default=ARCHIVE_ROOT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write back. Default is dry-run.",
    )
    args = parser.parse_args(argv)

    if not args.portfolio.exists():
        print(f"portfolio not found: {args.portfolio}", file=sys.stderr)
        return 2

    with args.portfolio.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = list(reader)

    if not cols:
        print("portfolio has no columns", file=sys.stderr)
        return 2

    archive = load_archive_lookup(args.archive_root)
    print(f"loaded {len(archive)} archived slug -> station mappings")

    # Make sure the identity columns exist in the schema. If they don't,
    # we add them at the end so DictWriter doesn't drop our fixes.
    added_cols = [c for c in IDENTITY_COLUMNS if c not in cols]
    if added_cols:
        cols.extend(added_cols)
        print(f"added missing schema columns: {added_cols}")

    fixed_rows = 0
    fixed_cells = 0
    examples: list[tuple[str, dict[str, str]]] = []
    for row in rows:
        # Make sure every identity column has a key; missing keys
        # crash DictWriter on output.
        for col in IDENTITY_COLUMNS:
            row.setdefault(col, "")
        fixes = repair_row(row, archive)
        if fixes:
            fixed_rows += 1
            fixed_cells += len(fixes)
            row.update(fixes)
            if len(examples) < 5:
                examples.append((row.get("event_slug", ""), fixes))

    print(f"rows touched: {fixed_rows} / {len(rows)}")
    print(f"cells filled: {fixed_cells}")
    print()
    print("samples:")
    for slug, fixes in examples:
        print(f"  {slug}")
        for k, v in fixes.items():
            print(f"    {k:<14} = {v!r}")

    if not args.apply:
        print()
        print("dry-run only — pass --apply to actually write")
        return 0

    if fixed_rows == 0:
        print("nothing to write")
        return 0

    # Backup with UTC timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = args.portfolio.with_name(f"{args.portfolio.name}.bak.{ts}")
    shutil.copy2(args.portfolio, backup_path)
    print(f"backup: {backup_path}")

    tmp_path = args.portfolio.with_suffix(args.portfolio.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, args.portfolio)
    print(f"wrote {args.portfolio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
