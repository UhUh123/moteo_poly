"""Chapter 4 diagnostic: what would Kelly sizing have done on our settled trades?

Read-only counterfactual. For every settled paper position we walk three
sizing policies and compute the resulting hypothetical PnL on the same
trade outcomes:

  - flat $0.25  (what the project actually did)
  - full Kelly: f* = (q - p) / (1 - p),  capped at the configured ceiling
  - quarter Kelly: f* / 4, same cap

The cap exists because Kelly on a single bet can prescribe absurd
fractions when q is close to 1.0 (which our model emits after near-close
refresh). On a real $100 bankroll a per-trade cap of 1-2% is the
industry norm; we honor that here so the comparison is honest.

This is a *retrospective*, not a recommendation. Chapter 4 §10 lists the
four conditions that must hold before we'd actually switch off flat
sizing — none of them hold yet.

Usage:
    PYTHONPATH=src python3 scripts/measure_kelly_counterfactual.py
    PYTHONPATH=src python3 scripts/measure_kelly_counterfactual.py --bankroll 100 --cap-pct 0.02
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO = ROOT / "artifacts" / "paper_portfolio.csv"

SETTLED_STATUSES = {"won", "lost"}


def _f(v, default=None):
    if v in {None, ""}:
        return default
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _kelly_fraction(q: float, p: float) -> float:
    """Kelly fraction for buying a YES-style contract: f* = (q - p) / (1 - p).

    Returns 0 when there is no edge (q <= p) or the contract has no
    upside left (p >= 1)."""
    if p <= 0 or p >= 1 or q <= p:
        return 0.0
    return (q - p) / (1 - p)


def _hypothetical_pnl(stake_usdc: float, price: float, fee: float, won: bool) -> float:
    """Apply chapter 3 EV math at the realised outcome.

    Number of shares the stake bought = stake / (price + fee).
    On a win each share pays $1; on a loss each share pays $0.
    """
    denom = price + fee
    if denom <= 0:
        return 0.0
    shares = stake_usdc / denom
    payout = shares if won else 0.0
    return payout - stake_usdc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", default=str(DEFAULT_PORTFOLIO))
    parser.add_argument("--bankroll", type=float, default=100.0,
                        help="Bankroll used as the base for Kelly fractions.")
    parser.add_argument("--cap-pct", type=float, default=0.02,
                        help="Per-trade hard cap as a fraction of bankroll. "
                             "Industry norm is 1-2%% even when Kelly says more.")
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

    cap = args.bankroll * args.cap_pct
    print(f"Settled positions: {len(settled)}")
    print(f"Bankroll = ${args.bankroll:.2f}, per-trade cap = "
          f"{args.cap_pct*100:.1f}% (= ${cap:.2f})")
    print()
    print(f"{'#':>3} {'side':>7} {'price':>6} {'fair':>6} {'edge':>7}   "
          f"{'fullK%':>7} {'qrtK%':>7}   "
          f"{'flat$':>7} {'fullK$':>7} {'qrtK$':>7}   "
          f"{'pnl_flat':>9} {'pnl_full':>9} {'pnl_qrt':>9}  won")
    print("-" * 130)

    totals = {
        "flat_stake": 0.0, "full_stake": 0.0, "quarter_stake": 0.0,
        "flat_pnl": 0.0, "full_pnl": 0.0, "quarter_pnl": 0.0,
        "flat_loss_max": 0.0, "full_loss_max": 0.0, "quarter_loss_max": 0.0,
        "wins": 0, "losses": 0,
    }

    for idx, row in enumerate(settled, 1):
        side = row.get("side", "")
        price = _f(row.get("price"), 0.0) or 0.0
        fee = _f(row.get("fee_per_share"), 0.0) or 0.0
        fair = _f(row.get("fair_probability"), 0.0) or 0.0
        flat_stake = _f(row.get("stake_usdc"), 0.0) or 0.0
        won = row.get("status") == "won"

        # Kelly's q/p reference frame is "the side we bought".
        # signals.py:212 already stores fair = fair_yes if side==BUY_YES else fair_no.
        # So we can apply Kelly's (q - p)/(1 - p) directly.
        edge = fair - price
        full_kelly = _kelly_fraction(fair, price)
        quarter_kelly = full_kelly * 0.25

        full_stake = min(full_kelly * args.bankroll, cap)
        quarter_stake = min(quarter_kelly * args.bankroll, cap)

        pnl_flat = _hypothetical_pnl(flat_stake, price, fee, won)
        pnl_full = _hypothetical_pnl(full_stake, price, fee, won)
        pnl_quarter = _hypothetical_pnl(quarter_stake, price, fee, won)

        print(f"{idx:>3} {side:>7} {price:>6.3f} {fair:>5.1%} "
              f"{edge:+6.3f}   "
              f"{full_kelly*100:>6.2f}% {quarter_kelly*100:>6.2f}%   "
              f"{flat_stake:>6.2f} {full_stake:>6.2f} {quarter_stake:>6.2f}   "
              f"{pnl_flat:>+8.3f} {pnl_full:>+8.3f} {pnl_quarter:>+8.3f}  "
              f"{'W' if won else 'L'}")

        totals["flat_stake"] += flat_stake
        totals["full_stake"] += full_stake
        totals["quarter_stake"] += quarter_stake
        totals["flat_pnl"] += pnl_flat
        totals["full_pnl"] += pnl_full
        totals["quarter_pnl"] += pnl_quarter
        if not won:
            totals["flat_loss_max"] = max(totals["flat_loss_max"], flat_stake)
            totals["full_loss_max"] = max(totals["full_loss_max"], full_stake)
            totals["quarter_loss_max"] = max(totals["quarter_loss_max"], quarter_stake)
        totals["wins"] += int(won)
        totals["losses"] += int(not won)

    n = len(settled)
    print()
    print(f"--- Totals over {n} settled positions ({totals['wins']}W / {totals['losses']}L) ---")
    print(f"  {'metric':<32} {'flat $0.25':>13} {'full Kelly':>13} {'quarter Kelly':>15}")
    print(f"  {'total stake':<32} "
          f"{totals['flat_stake']:>11.2f}$ "
          f"{totals['full_stake']:>11.2f}$ "
          f"{totals['quarter_stake']:>13.2f}$")
    print(f"  {'realised PnL':<32} "
          f"{totals['flat_pnl']:>+11.2f}$ "
          f"{totals['full_pnl']:>+11.2f}$ "
          f"{totals['quarter_pnl']:>+13.2f}$")
    pnl_per_trade_flat = totals['flat_pnl'] / n if n else 0
    pnl_per_trade_full = totals['full_pnl'] / n if n else 0
    pnl_per_trade_quarter = totals['quarter_pnl'] / n if n else 0
    print(f"  {'avg PnL per trade':<32} "
          f"{pnl_per_trade_flat:>+11.4f}$ "
          f"{pnl_per_trade_full:>+11.4f}$ "
          f"{pnl_per_trade_quarter:>+13.4f}$")
    print(f"  {'biggest single loss':<32} "
          f"{-totals['flat_loss_max']:>+11.2f}$ "
          f"{-totals['full_loss_max']:>+11.2f}$ "
          f"{-totals['quarter_loss_max']:>+13.2f}$")

    print()
    print("Reading guide:")
    print("  - 'fullK%' is what Kelly says to put on this bet given our q at entry.")
    print("    Our fair_probability after near-close refresh is often >0.95, which")
    print("    drives Kelly toward 50%+ of bankroll on a single bet. The cap")
    print("    is what keeps it reasonable.")
    print("  - The realised-PnL columns show the trade-off: Kelly stakes more,")
    print("    so wins are bigger AND losses are bigger. On 22 trades this is")
    print("    not enough sample to pick a winner.")
    print("  - Chapter 4 §10 lists the conditions to actually switch sizing")
    print("    policy. None of them hold yet (need 200+ settled, calibrated MAE,")
    print("    confirmed positive PnL, fat-tails handled).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
