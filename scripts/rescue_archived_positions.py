"""One-shot: merge previously-archived paper portfolios into current file.

Rescues positions that `daily_open_trades` accidentally wiped before we
fixed it to preserve open rows. Deduplicates by (event_slug, side),
backs up the current portfolio, and writes the merged result in place.
"""
from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path


ROOT = Path(r"C:\poly\detect-temperature")


def main(archive_names: list[str]) -> int:
    cur = ROOT / "artifacts" / "paper_portfolio.csv"
    bak = ROOT / "artifacts" / "paper_portfolio.beforerescue.csv"
    shutil.copy2(cur, bak)
    print(f"backup -> {bak}")

    with cur.open(newline="", encoding="utf-8") as fh:
        cur_rows = list(csv.DictReader(fh))
    print(f"current rows: {len(cur_rows)}")

    keys = {(r.get("event_slug", ""), r.get("side", "")) for r in cur_rows}
    fields = list(cur_rows[0].keys()) if cur_rows else []
    added = 0

    for arch_name in archive_names:
        arch = ROOT / "artifacts" / "paper_runs" / arch_name / "paper_portfolio.csv"
        if not arch.exists():
            print(f"SKIP missing: {arch}")
            continue
        with arch.open(newline="", encoding="utf-8") as fh:
            arch_rows = list(csv.DictReader(fh))
        for row in arch_rows:
            k = (row.get("event_slug", ""), row.get("side", ""))
            if k in keys:
                continue
            # add any new columns the archive has
            for c in row.keys():
                if c not in fields:
                    fields.append(c)
            cur_rows.append(row)
            keys.add(k)
            added += 1
        print(f"after {arch_name}: rescued so far {added}, total rows {len(cur_rows)}")

    with cur.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in cur_rows:
            writer.writerow({col: row.get(col, "") for col in fields})

    print(f"\nDONE rescued={added} total={len(cur_rows)} -> {cur}")
    return 0


if __name__ == "__main__":
    archives = sys.argv[1:] if len(sys.argv) > 1 else [
        "20260511T115840Z-pre-open",
        "20260511T120748Z-pre-open",
    ]
    sys.exit(main(archives))
