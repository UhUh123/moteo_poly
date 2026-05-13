"""Diagnose the empirical distribution of forecast residuals.

Used to verify the central claim of guide/chapter_02_probability.md:
"Real temperature forecast errors have fat tails, not Gaussian."

Read-only. Does not change any model, signal, or trade. Output is meant
for reading next to the chapter to anchor the abstract claim in concrete
project data.

Usage:
    PYTHONPATH=src python3 scripts/measure_residual_distribution.py
    PYTHONPATH=src python3 scripts/measure_residual_distribution.py --by-extreme
    PYTHONPATH=src python3 scripts/measure_residual_distribution.py --top-stations 5

Computes for the training_real.csv corpus (~124k rows of paired
forecast / observed daily extremes, 51 ICAO stations, 2023-01..2026-04):
  - mean (bias) and sigma of the forecast residual = observed - forecast
  - skewness and excess kurtosis (Gaussian = 0; positive = fat tails)
  - frequency of |error| > k*sigma for k = 2, 3, 4, 5, 6
  - the same expected under a pure Gaussian
  - the empirical-to-Gaussian ratio (this is "how much the Gaussian
    underestimates the tails"; a number much greater than 1 means
    Normal CDF will systematically misprice the bucket)

Compare against signals.py:normal_interval_probability and
near_close.py:refined_bucket_probability — both assume the residual
is Gaussian.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAINING = ROOT / "data" / "training_real.csv"


def _residual_for_row(row: dict) -> float | None:
    """Pick max-vs-forecast_max or min-vs-forecast_min depending on target."""
    extreme = row.get("target_extreme", "")
    try:
        observed = float(row.get("observed_temp_c"))
    except (TypeError, ValueError):
        return None
    if extreme == "max":
        col = "forecast_temp_max_c"
    elif extreme == "min":
        col = "forecast_temp_min_c"
    else:
        return None
    try:
        forecast = float(row.get(col))
    except (TypeError, ValueError):
        return None
    return observed - forecast


def _moments(errs: list[float]) -> dict:
    n = len(errs)
    if n == 0:
        return {"n": 0}
    mean = sum(errs) / n
    var = sum((e - mean) ** 2 for e in errs) / n
    sigma = var ** 0.5
    if sigma == 0:
        return {"n": n, "mean": mean, "sigma": 0.0, "skew": 0.0, "excess_kurtosis": 0.0}
    m3 = sum((e - mean) ** 3 for e in errs) / n
    m4 = sum((e - mean) ** 4 for e in errs) / n
    skew = m3 / sigma ** 3
    kurt_pearson = m4 / sigma ** 4
    return {
        "n": n,
        "mean": round(mean, 4),
        "sigma": round(sigma, 4),
        "skew": round(skew, 4),
        "excess_kurtosis": round(kurt_pearson - 3.0, 4),
    }


def _gaussian_two_tail(z: float) -> float:
    """P(|X| > z) for X ~ N(0, 1)."""
    return 1 - math.erf(z / math.sqrt(2))


def _tail_table(errs: list[float], sigma: float) -> list[dict]:
    n = len(errs)
    rows = []
    for k in (2, 3, 4, 5, 6):
        threshold = k * sigma
        count = sum(1 for e in errs if abs(e) > threshold)
        observed = count / n if n else 0.0
        gauss = _gaussian_two_tail(k)
        ratio = (observed / gauss) if gauss > 0 else float("inf")
        rows.append({
            "k": k,
            "threshold_c": round(threshold, 3),
            "observed_count": count,
            "observed_freq": observed,
            "gauss_freq": gauss,
            "ratio": ratio,
        })
    return rows


def _print_block(title: str, stats: dict, tails: list[dict] | None = None) -> None:
    print(f"\n=== {title} ===")
    print(f"  n               = {stats['n']:,}")
    if stats["n"] == 0:
        return
    print(f"  mean (bias)     = {stats['mean']:+.3f} C")
    print(f"  sigma           = {stats['sigma']:.3f} C")
    print(f"  skewness        = {stats['skew']:+.3f}    (Gaussian = 0)")
    print(f"  excess kurtosis = {stats['excess_kurtosis']:+.3f}    (Gaussian = 0; positive = fat tails)")
    if not tails:
        return
    print(f"  {'|err|>':<10} {'threshold':<12} {'observed':<22} {'Gaussian':<22} {'ratio':<10}")
    for row in tails:
        actual_freq = row["observed_freq"]
        if actual_freq == 0:
            actual_str = "never"
        else:
            actual_str = f"1 / {1 / actual_freq:>10,.0f}"
        gauss_freq = row["gauss_freq"]
        gauss_str = f"1 / {1 / gauss_freq:>14,.0f}" if gauss_freq > 0 else "never"
        ratio = row["ratio"]
        if ratio == float("inf") or ratio > 1e9:
            ratio_str = "inf"
        else:
            ratio_str = f"{ratio:>8,.1f}x"
        print(f"  {row['k']:>2}*sigma    "
              f"{row['threshold_c']:>5.2f} C    "
              f"{actual_str:<22} {gauss_str:<22} {ratio_str}")


def _largest(errs: list[float], n: int = 10) -> list[float]:
    return sorted(errs, key=abs, reverse=True)[:n]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Empirical residual distribution diagnostic.")
    parser.add_argument("--training", default=str(DEFAULT_TRAINING))
    parser.add_argument("--by-extreme", action="store_true",
                        help="Also break down by target_extreme (max vs min).")
    parser.add_argument("--top-stations", type=int, default=0,
                        help="Show stats for the N stations with most rows.")
    parser.add_argument("--worst", type=int, default=10,
                        help="Print this many worst absolute residuals.")
    args = parser.parse_args(argv)

    path = Path(args.training)
    if not path.exists():
        print(f"missing: {path}", file=sys.stderr)
        return 1

    all_errs: list[float] = []
    by_extreme: dict[str, list[float]] = defaultdict(list)
    by_station: dict[str, list[float]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            err = _residual_for_row(row)
            if err is None:
                continue
            all_errs.append(err)
            by_extreme[row.get("target_extreme", "?")].append(err)
            by_station[row.get("station_id", "?")].append(err)

    overall = _moments(all_errs)
    tails = _tail_table(all_errs, overall.get("sigma", 0.0))
    _print_block("Overall residuals (forecast bias, all stations, both extremes)",
                 overall, tails)

    if args.by_extreme:
        for extreme in ("max", "min"):
            sub = by_extreme.get(extreme, [])
            stats = _moments(sub)
            sub_tails = _tail_table(sub, stats.get("sigma", 0.0))
            _print_block(f"By target_extreme = {extreme!r}", stats, sub_tails)

    if args.top_stations > 0:
        ranked = sorted(by_station.items(), key=lambda kv: -len(kv[1]))[: args.top_stations]
        for station_id, errs in ranked:
            _print_block(f"Station {station_id}", _moments(errs))

    if args.worst > 0:
        worst = _largest(all_errs, args.worst)
        print()
        print(f"Top {args.worst} worst absolute residuals (signed): "
              f"{[round(e, 2) for e in worst]}")

    print()
    print("Reading guide for these numbers:")
    print("  - excess_kurtosis > 0 means tails are fatter than Gaussian.")
    print("  - The 'ratio' column is how often a |k*sigma| miss really happens,")
    print("    divided by how often Gaussian says it should. >>1 = Gaussian")
    print("    underestimates the tail. The further you go in k, the worse the")
    print("    underestimation tends to get for fat-tailed data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
