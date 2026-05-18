"""One-shot inspection of market_signals.csv schema."""
import csv
from pathlib import Path

p = Path(r"C:\poly\detect-temperature\artifacts\market_signals.csv")
with p.open("r", encoding="utf-8") as fh:
    reader = csv.DictReader(fh)
    cols = reader.fieldnames or []
    print(f"total cols: {len(cols)}")
    interesting = [c for c in cols if c in {
        "target_date", "station_id", "target_extreme", "city",
        "event_slug", "interval_unit", "interval_lower", "interval_upper",
        "target_unit", "location_name", "source_domain"
    }]
    print(f"interesting cols present: {interesting}")
    for row in reader:
        for c in interesting:
            v = (row.get(c) or "").strip()
            print(f"  {c:<20} = {v[:50]!r}")
        break
