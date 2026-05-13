"""Chapter 3 diagnostic: apply expected-value math to settled paper positions.

Reads artifacts/paper_portfolio.csv, walks every settled row, and computes:

  - break_even_win_rate  = the entry price (per chapter 3 §8: "break-even
    win rate equals the price you paid")
  - per_share_payout     = (1 - price) on win, -price on loss
  - per_share_ev_at_entry = fair_probability - price  (equivalent to q - p
    in the chapter; minus per-share fee if present)
  - realized PnL per position and aggregate
  - tally: actual win rate vs implied break-even rate

Read-only. Does not change paper state, does not place orders, does not
modify health.json. Strict pure math on data already on disk.

Usage:
    PYTHONPATH=src python3 scripts/measure_settled_ev.py
    PYTHONPATH=src python3 scripts/measure_settled_ev.py --portfolio path/to/paper_portfolio.csv
    PYTHONPATH=src python3 scripts/measure_settled_ev.py --by-side
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO = ROOT / "artifacts" / "paper_portfolio.csv"


SETTLED_STATUSES = {"won", "lost"}


def _f(value: object, default: float | None = None) -> float | None:
    if value in {None, ""}:
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _classify(price: float, side: str) -> dict:
    """For a single settled row: math from chapter 3."""
    win_payout = 1.0 - price        # what you get extra if the bucket goes your way
    loss_payout = -price             # what you lose otherwise
    break_even_p = price             # P(win) needed for EV = 0 (chapter 3 §8)
    return {
        "side": side,
        "price": round(price, 4),
        "break_even_win_rate": round(break_even_p, 4),
        "per_share_win": round(win_payout, 4),
        "per_share_loss": round(loss_payout, 4),
    }


def _per_share_ev(fair: float, price: float, fee: float) -> float:
    """EV per share given (fair_yes_or_no, our_entry_price, per_share_fee)."""
    return (fair * (1 - price)) + ((1 - fair) * (-price)) - fee


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", default=str(DEFAULT_PORTFOLIO))
    parser.add_argument("--by-side", action="store_true",
                        help="Group results by BUY_YES vs BUY_NO.")
    args = parser.parse_args(argv)

    path = Path(args.portfolio)
    if not path.exists():
        print(f"missing: {path}", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    settled = [r for r in rows if r.get("status") in SETTLED_STATUSES]
    if not settled:
        print("no settled rows yet")
        return 0

    print(f"Settled positions: {len(settled)} / {len(rows)} total\n")

    # Per-position table
    print(f"{'#':>3} {'side':>7} {'price':>6} {'be%':>6} {'fair%':>7} "
          f"{'ev/sh':>8} {'won':>4} {'pnl':>8}  event_slug")
    print("-" * 112)

    by_side: dict[str, list[dict]] = defaultdict(list)
    aggregates = {
        "total_stake": 0.0, "total_payout": 0.0, "total_pnl": 0.0,
        "wins": 0, "losses": 0,
        "ev_at_entry_total": 0.0,
        "implied_breakeven_sum": 0.0,
    }

    for idx, row in enumerate(settled, 1):
        side = row.get("side", "")
        price = _f(row.get("price"), 0.0) or 0.0
        fee = _f(row.get("fee_per_share"), 0.0) or 0.0
        fair = _f(row.get("fair_probability"), 0.0) or 0.0
        stake = _f(row.get("stake_usdc"), 0.0) or 0.0
        won = (row.get("status") == "won")
        pnl = _f(row.get("pnl_usdc"), 0.0) or 0.0

        cls = _classify(price, side)
        ev_per_share = _per_share_ev(fair, price, fee)
        slug = (row.get("event_slug") or "")[:55]

        print(f"{idx:>3} {side:>7} {price:>6.3f} "
              f"{cls['break_even_win_rate']*100:>5.1f}% "
              f"{fair*100:>6.1f}% "
              f"{ev_per_share:>+7.4f} "
              f"{'W' if won else 'L':>4} "
              f"{pnl:>+7.3f}  {slug}")

        by_side[side].append({
            "price": price, "fee": fee, "fair": fair, "stake": stake,
            "won": won, "pnl": pnl, "ev_per_share": ev_per_share,
        })
        aggregates["total_stake"] += stake
        aggregates["total_pnl"] += pnl
        aggregates["wins"] += int(won)
        aggregates["losses"] += int(not won)
        aggregates["ev_at_entry_total"] += stake * ev_per_share / max(price, 1e-9)
        aggregates["implied_breakeven_sum"] += price

    # Aggregate
    n = len(settled)
    win_rate = aggregates["wins"] / n
    avg_be = aggregates["implied_breakeven_sum"] / n
    avg_ev = aggregates["ev_at_entry_total"] / n if n else 0.0
    print()
    print(f"--- Aggregate over {n} settled positions ---")
    print(f"  total stake:       ${aggregates['total_stake']:.2f}")
    print(f"  total realised PnL: ${aggregates['total_pnl']:+.2f}")
    print(f"  realised win rate: {win_rate*100:.1f}%   ({aggregates['wins']}W / {aggregates['losses']}L)")
    print(f"  avg implied break-even (mean entry price): {avg_be*100:.1f}%")
    print(f"  win-rate vs break-even gap: {(win_rate - avg_be)*100:+.1f} percentage points")
    print(f"  per-position EV at entry (avg by stake-implied):  ${avg_ev:+.4f}")

    if win_rate < avg_be:
        print()
        print(f"  Below break-even by {(avg_be-win_rate)*100:.1f}pp. Negative EV is expected,")
        print(f"  not 'bad luck' — chapter 3 §9 talks about exactly this.")
    elif win_rate >= avg_be:
        print()
        print(f"  Above break-even. Realised win rate exceeds the average price")
        print(f"  paid. This is consistent with positive EV at this sample size.")

    if args.by_side:
        for side, sub in sorted(by_side.items()):
            wins = sum(1 for r in sub if r["won"])
            n_side = len(sub)
            wr = wins / n_side
            be = mean(r["price"] for r in sub)
            pnl = sum(r["pnl"] for r in sub)
            print()
            print(f"--- {side}: {n_side} settled ---")
            print(f"  win rate: {wr*100:.1f}%   break-even (avg price): {be*100:.1f}%")
            print(f"  win-rate − break-even: {(wr-be)*100:+.1f}pp")
            print(f"  realised PnL: ${pnl:+.2f}")

    print()
    print("Notes:")
    print("  - 'be%' = break-even win rate = entry price (chapter 3 §8).")
    print("  - 'ev/sh' = q - p - fee, where q is fair_probability at entry.")
    print("    A positive number means our model thought the trade was profitable.")
    print("  - Whether the realised win rate beats break-even is the only honest")
    print("    diagnostic on a small sample. PnL noise on n<50 is huge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
