"""Inspect why settle_agreement is blank on most PM-authoritative rows."""
import csv
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:\poly\detect-temperature")
rows = list(csv.DictReader((ROOT / "artifacts" / "paper_portfolio.csv").open(newline="", encoding="utf-8")))

# All PM-authoritative settled
pm_settled = [r for r in rows if r.get("settle_authority") == "polymarket_resolved"]
print(f"PM-authoritative settled: {len(pm_settled)}")

agree_values = Counter(r.get("settle_agreement", "") for r in pm_settled)
print(f"settle_agreement values: {dict(agree_values)}")
print()

# Check what fields exist on rows with blank settle_agreement
blank = [r for r in pm_settled if not r.get("settle_agreement")]
print(f"--- sample blank-agreement rows ---")
for r in blank[:5]:
    print(f"  slug={r.get('event_slug','')[:40]}")
    print(f"    actual_status={r.get('actual_status','')!r}")
    print(f"    polymarket_yes_won={r.get('polymarket_yes_won','')!r}")
    print(f"    polymarket_uma_status={r.get('polymarket_uma_status','')!r}")
    print(f"    settle_agreement={r.get('settle_agreement','')!r}")
    print(f"    won={r.get('won','')!r}, side={r.get('side','')!r}")
    print(f"    actual_value={r.get('actual_value','')!r}, observed_so_far_c={r.get('observed_so_far_c','')!r}")
    print()

# Cross-check: do these have valid PM data?
pm_data_present = sum(1 for r in blank if r.get("polymarket_yes_won") in {"0", "1"})
print(f"blank-agreement rows with PM yes_won set: {pm_data_present} / {len(blank)}")
