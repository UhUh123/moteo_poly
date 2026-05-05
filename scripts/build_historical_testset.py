#!/usr/bin/env python3
"""Build historical observed + forecast testset for available period."""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from detect_temperature.sources.historical import OpenMeteoHistoricalProvider
from detect_temperature.sources.manual import ManualStationCatalog
from detect_temperature.sources.aviation_weather import AviationWeatherStationCatalog
from detect_temperature.sources.base import CompositeStationCatalog


def main() -> int:
    provider = OpenMeteoHistoricalProvider()

    catalogs = []
    manual_path = ROOT / "data" / "manual_stations.csv"
    if manual_path.exists():
        catalogs.append(ManualStationCatalog(manual_path))
    cache_path = ROOT / "data" / "stations.cache.json"
    if cache_path.exists():
        catalogs.append(AviationWeatherStationCatalog(cache_path))
    catalog = CompositeStationCatalog(catalogs) if catalogs else None

    # Read unique stations from features.csv
    features_path = ROOT / "data" / "features.csv"
    station_ids = set()
    with features_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sid = row.get("station_id", "")
            if sid:
                station_ids.add(sid)

    if not catalog:
        print("No station catalog available")
        return 1

    output_path = ROOT / "data" / "historical_observed.csv"
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["station_id", "date", "observed_max_c", "observed_min_c"],
        )
        writer.writeheader()

        # Use March 2026 — both forecast and archive APIs should work here
        start = date(2026, 3, 1)
        end = date(2026, 3, 31)

        for sid in sorted(station_ids):
            station = catalog.lookup(sid)
            if station is None:
                print(f"  skip {sid}: not in catalog")
                continue
            print(f"  fetching {sid} ({start} to {end}) ...")
            try:
                rows = provider.daily_extremes(station, start, end)
                for row in rows:
                    writer.writerow(row)
                print(f"    -> {len(rows)} days")
            except Exception as exc:
                print(f"    ERROR: {exc}")

    print(f"\nDone -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
