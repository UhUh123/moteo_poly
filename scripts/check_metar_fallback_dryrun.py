"""One-shot sanity check for the new METAR actuals fallback.

Reads the still-open paper positions, picks the ones whose primary
provider is currently failing (weather.com 403 for non-US ICAO), and
prints what the fallback provider would return WITHOUT touching
actuals.csv. Read-only — safe to run anytime.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from detect_temperature.schema import MarketTarget
from detect_temperature.sources.actuals import MetarHistoryActualsProvider
from detect_temperature.sources.aviation_weather import AviationWeatherStationCatalog
from detect_temperature.sources.manual import ManualStationCatalog
from detect_temperature.sources.base import CompositeStationCatalog


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    aviation = AviationWeatherStationCatalog(cache_path=ROOT / "data" / "stations.cache.json")
    manual = ManualStationCatalog(path=ROOT / "data" / "manual_stations.csv")
    catalog = CompositeStationCatalog([manual, aviation])

    portfolio = ROOT / "artifacts" / "paper_portfolio.csv"
    actuals = ROOT / "data" / "actuals.csv"
    actual_status = {}
    with actuals.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            actual_status[row.get("slug", "")] = row

    open_rows = []
    with portfolio.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") in {"open", "at_risk", "pending_actual"}:
                open_rows.append(row)

    print(f"still-open paper positions: {len(open_rows)}")
    print()

    provider = MetarHistoryActualsProvider(history_root=ROOT / "data" / "metar_history")
    seen_slugs: set[str] = set()
    bucket_ok = 0
    bucket_pending = 0

    for row in open_rows:
        slug = row.get("event_slug") or row.get("market_slug") or ""
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        actual_row = actual_status.get(slug, {})
        primary_status = (actual_row.get("status") or "").strip()
        primary_notes = (actual_row.get("notes") or "").strip()

        # Slug parser fragments — we already do this in pipeline; re-derive
        # the minimum we need here.
        target_date = _parse_target_date(slug)
        target_extreme = "min" if slug.startswith("lowest-") else "max"
        target_unit = (row.get("interval_unit") or "celsius").strip().lower() or "celsius"
        station_id = (row.get("station_id") or actual_row.get("station_id") or "").upper().strip()
        if not target_date or not station_id:
            continue

        target = MarketTarget(
            title=slug,
            slug=slug,
            city=row.get("city", ""),
            location_name="",
            target_date=target_date,
            target_extreme=target_extreme,
            target_unit=target_unit,
            station_id=station_id,
            resolution_source_url="",
            source_domain="wunderground.com",
            description="",
        )
        station = catalog.lookup(station_id)
        result = provider.collect(target, station)
        marker = "OK " if result.status == "ok" else "PEND"
        if result.status == "ok":
            bucket_ok += 1
        else:
            bucket_pending += 1
        lon = getattr(station, "longitude", None) if station else None
        country = getattr(station, "country", "") if station else ""
        print(
            f"{marker} {station_id:<5} lon={lon if lon is not None else '?':>7} {country:<3} "
            f"primary={primary_status:<7} | metar -> samples={result.sample_count:>3} "
            f"temp={result.observed_temp_c} ({slug[:60]}) "
            f"primary_note={primary_notes[:60]}"
        )

    print()
    print(f"summary: ok={bucket_ok}  pending={bucket_pending}  total_unique_slugs={len(seen_slugs)}")


def _parse_target_date(slug: str) -> date | None:
    parts = slug.split("-on-")
    if len(parts) != 2:
        return None
    suffix = parts[1]
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
        "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    tokens = suffix.split("-")
    if len(tokens) < 3:
        return None
    month_name = tokens[0].lower()
    if month_name not in months:
        return None
    try:
        day = int(tokens[1])
        year = int(tokens[2])
    except ValueError:
        return None
    try:
        return date(year, months[month_name], day)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
