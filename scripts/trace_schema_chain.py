"""Trace which columns make it through the targets -> predictions -> signals chain."""
import csv
from pathlib import Path

ROOT = Path(r"C:\poly\detect-temperature")
WANTED = ["target_date", "station_id", "target_extreme", "city",
          "event_slug", "interval_unit", "target_unit",
          "location_name", "source_domain", "interval_lower", "interval_upper"]

for rel in ["data/targets.csv", "artifacts/predictions_gbm.csv", "artifacts/market_signals.csv"]:
    p = ROOT / rel
    if not p.exists():
        print(f"{rel:<40} MISSING")
        continue
    with p.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        present = [c for c in WANTED if c in cols]
        missing = [c for c in WANTED if c not in cols]
        print(f"{rel:<40} cols={len(cols):>3}")
        print(f"  has    : {present}")
        print(f"  missing: {missing}")
        for row in reader:
            sample = {c: (row.get(c) or "")[:30] for c in present}
            print(f"  sample: {sample}")
            break
        print()
