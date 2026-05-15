"""Re-collect actuals for event_slugs still living in paper_portfolio.csv.

Use case: `daily_open_trades` rotated `targets.csv` away from yesterday's
slugs, and the old pre-merge `collect_actuals` then wiped the resolved
rows for those slugs. This script rebuilds minimal `MarketTarget` objects
from the paper portfolio rows (slug + station + target_date + extreme +
unit) and re-fetches actuals for each, merging into `data/actuals.csv`.

Safe to run repeatedly: the merge logic protects ok rows from being
downgraded to pending.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

from detect_temperature.schema import MarketTarget
from detect_temperature.sources.actuals import (
    collect_actual_for_target,
    error_actual_for_target,
)
from detect_temperature.sources.aviation_weather import AviationWeatherStationCatalog
from detect_temperature.sources.base import CompositeStationCatalog
from detect_temperature.sources.manual import ManualStationCatalog
from detect_temperature.pipeline import read_records_csv, write_records_csv


ROOT = Path(r"C:\poly\detect-temperature")

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _parse_date_from_slug(slug: str) -> date | None:
    match = re.search(r"on-([a-z]+)-(\d+)-(\d{4})$", slug)
    if not match:
        return None
    month_name, day, year = match.groups()
    m = MONTHS.get(month_name.lower())
    if not m:
        return None
    try:
        return date(int(year), m, int(day))
    except ValueError:
        return None


def main() -> int:
    portfolio_csv = ROOT / "artifacts" / "paper_portfolio.csv"
    targets_csv = ROOT / "data" / "targets.csv"
    actuals_csv = ROOT / "data" / "actuals.csv"

    known_station: dict[str, str] = {}
    known_unit: dict[str, str] = {}
    if targets_csv.exists():
        for row in read_records_csv(targets_csv):
            known_station[row.get("slug", "")] = row.get("station_id", "")
            known_unit[row.get("slug", "")] = row.get("target_unit", "celsius")

    # Fallback: walk archived targets.csv files in artifacts/paper_runs/.
    # The current targets.csv only has tomorrow's slugs after the daily
    # rotation, so any paper position older than ~24h whose slug fell out
    # of targets.csv will look like "no station_id" and be skipped. Each
    # paper_runs/<ts>-pre-open/targets.csv is an archive from when that
    # day's open trade fired - they together cover every slug we ever
    # opened a position on.
    paper_runs = ROOT / "artifacts" / "paper_runs"
    archived = sorted(paper_runs.glob("*/targets.csv")) if paper_runs.exists() else []
    for archived_targets in archived:
        try:
            for row in read_records_csv(archived_targets):
                slug = row.get("slug", "")
                if not slug:
                    continue
                # Don't overwrite info from current targets.csv - it's freshest
                if slug not in known_station:
                    sid = row.get("station_id", "")
                    if sid:
                        known_station[slug] = sid
                if slug not in known_unit:
                    unit = row.get("target_unit", "")
                    if unit:
                        known_unit[slug] = unit
        except Exception as exc:
            print(f"  WARN: failed to read {archived_targets}: {exc}")

    print(f"  loaded {len(known_station)} slug -> station_id mappings "
          f"({len([1 for v in known_station.values() if v])} non-empty)")

    catalog = CompositeStationCatalog([
        ManualStationCatalog(ROOT / "data" / "manual_stations.csv"),
        AviationWeatherStationCatalog(cache_path=ROOT / "data" / "stations.cache.json"),
    ])

    paper = read_records_csv(portfolio_csv)
    queue: list[MarketTarget] = []
    for pos in paper:
        slug = pos.get("event_slug") or ""
        if not slug:
            continue
        target_date = _parse_date_from_slug(slug)
        if target_date is None:
            print(f"SKIP {slug}: cannot parse date")
            continue
        if "highest-temperature" in slug:
            extreme = "max"
        elif "lowest-temperature" in slug:
            extreme = "min"
        else:
            print(f"SKIP {slug}: unknown extreme")
            continue
        station_id = known_station.get(slug, "") or (pos.get("station_id") or "").strip()
        if not station_id:
            print(f"SKIP {slug}: no station_id")
            continue
        unit = known_unit.get(slug) or pos.get("interval_unit") or "celsius"
        if station_id == "HKO":
            domain = "weather.gov.hk"
            url = "https://www.weather.gov.hk/"
            desc = "Hong Kong Observatory"
        else:
            domain = "wunderground.com"
            url = f"https://www.wunderground.com/weather/{station_id}"
            desc = ""

        queue.append(MarketTarget(
            title=slug, slug=slug, city="", location_name="",
            target_date=target_date, target_extreme=extreme, target_unit=unit,
            station_id=station_id, resolution_source_url=url,
            source_domain=domain, description=desc,
        ))

    print(f"queued {len(queue)} targets to re-fetch")

    station_cache: dict[str, object] = {}
    fresh: list[dict] = []
    ok_count = 0
    for t in queue:
        if t.station_id not in station_cache:
            station_cache[t.station_id] = catalog.lookup(t.station_id)
        station = station_cache[t.station_id]
        try:
            actual = collect_actual_for_target(t, station, finalization_lag_days=1)
        except Exception as exc:
            actual = error_actual_for_target(t, str(exc))
        rec = actual.to_record()
        fresh.append(rec)
        if rec.get("status") == "ok":
            ok_count += 1
            print(f"  ok   {t.slug}  {rec.get('observed_resolution_value')}")
        else:
            print(f"  {rec.get('status','?'):7} {t.slug}  {str(rec.get('notes',''))[:60]}")

    existing: dict[str, dict] = {}
    if actuals_csv.exists():
        for row in read_records_csv(actuals_csv):
            existing[row.get("slug", "")] = row
    merged = dict(existing)
    for row in fresh:
        slug = row.get("slug", "")
        if not slug:
            continue
        old = merged.get(slug)
        if old is None or row.get("status") == "ok" or old.get("status") != "ok":
            merged[slug] = row

    write_records_csv(list(merged.values()), actuals_csv)
    print(f"\nfetched={len(fresh)}  ok={ok_count}  actuals_total={len(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
