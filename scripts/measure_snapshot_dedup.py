"""One-shot dedup measurement on data/history/.

Walks every regular snapshot, hashes each tracked file, and reports
how often identical content shows up across snapshots. Used to
calibrate the cost/benefit of expanding state_archive's pool to
events.json + orderbooks.json. Read-only, safe to run anytime.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "history"
TRACKED = (
    "polymarket_weather_markets.csv",
    "polymarket_weather_events.json",
    "polymarket_geoblock.json",
    "polymarket_orderbooks.json",
)


def _short_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not HISTORY.exists():
        print(f"no history dir: {HISTORY}")
        return 2

    counts = {f: 0 for f in TRACKED}
    bytes_total = {f: 0 for f in TRACKED}
    uniques = {f: set() for f in TRACKED}
    bytes_unique = {f: 0 for f in TRACKED}

    days = sorted(p for p in HISTORY.iterdir() if p.is_dir() and p.name != "_state")
    for d in days:
        for snap in sorted(p for p in d.iterdir() if p.is_dir() and p.name.endswith("-regular")):
            for f in TRACKED:
                p = snap / f
                if not p.exists():
                    continue
                size = p.stat().st_size
                bytes_total[f] += size
                counts[f] += 1
                sha = _short_sha(p)
                if sha not in uniques[f]:
                    uniques[f].add(sha)
                    bytes_unique[f] += size

    print(f"{'file':<35} {'count':>6} {'unique':>6} {'ratio':>7}   {'total_MB':>9} {'unique_MB':>10}  potential_savings_MB")
    print("-" * 110)
    for f in TRACKED:
        c = counts[f]
        u = len(uniques[f])
        if c == 0:
            continue
        ratio = c / u if u else 0
        tot = bytes_total[f] / (1024 * 1024)
        uniq = bytes_unique[f] / (1024 * 1024)
        savings = tot - uniq
        print(f"{f:<35} {c:>6d} {u:>6d} {ratio:>6.2f}x  {tot:>9.1f} {uniq:>10.1f}  {savings:>10.1f}")

    grand_total = sum(bytes_total.values()) / (1024 * 1024)
    grand_unique = sum(bytes_unique.values()) / (1024 * 1024)
    print("-" * 110)
    print(f"{'TOTAL':<35} {'':>6} {'':>6} {'':>7}   {grand_total:>9.1f} {grand_unique:>10.1f}  {grand_total-grand_unique:>10.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
