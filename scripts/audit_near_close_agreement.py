"""Audit near_close 'resolved_early_by_observation' verdicts against
final on-chain Polymarket resolution.

Question we're answering: when our pipeline closes a paper position
DURING the day from a partial intraday observation (chapter 6 §8 of
the learning guide), how often is the verdict still right after PM
finalises on-chain? Disagreements are the cost of the early-close
heuristic; agreements show it's pulling its weight.
"""
import csv
from pathlib import Path

ROOT = Path(r"C:\poly\detect-temperature")
p = ROOT / "artifacts" / "paper_portfolio.csv"
rows = list(csv.DictReader(p.open(newline="", encoding="utf-8")))

settled = [r for r in rows if r.get("status") in {"won", "lost"}]
print(f"total settled: {len(settled)}")

early = [r for r in settled if r.get("actual_status") == "resolved_early_by_observation"]
print(f"resolved_early total: {len(early)}")

early_pm = [r for r in early if r.get("settle_authority") == "polymarket_resolved"]
early_prelim = [r for r in early if r.get("settle_authority") == "actuals_preliminary"]
print(f"  PM-authoritative: {len(early_pm)}")
print(f"  still preliminary: {len(early_prelim)}")

agree = [r for r in early_pm if r.get("settle_agreement") == "agree"]
disagree = [r for r in early_pm if r.get("settle_agreement") == "disagree"]
unknown = [r for r in early_pm if r.get("settle_agreement") not in {"agree", "disagree"}]
print(f"  agree     : {len(agree)}")
print(f"  disagree  : {len(disagree)}")
print(f"  other     : {len(unknown)}")

if disagree:
    print()
    print("=== disagreements (early call vs PM final) ===")
    for r in disagree[:20]:
        slug = r.get("event_slug", "")
        side = r.get("side", "")
        our_won = r.get("won", "")
        pm_yes = r.get("polymarket_yes_won", "")
        rr = (r.get("refresh_reason", "") or "")[:60]
        corr = r.get("settle_correction_usdc", "")
        print(f"  {slug:<55} side={side:<7} our_won={our_won} pm_yes={pm_yes} corr={corr}  | {rr}")

non_early = [
    r for r in settled
    if r.get("actual_status") != "resolved_early_by_observation"
    and r.get("settle_authority") == "polymarket_resolved"
]
ne_agree = sum(1 for r in non_early if r.get("settle_agreement") == "agree")
ne_disagree = sum(1 for r in non_early if r.get("settle_agreement") == "disagree")
print()
print(f"NON-early PM-authoritative: {len(non_early)}")
print(f"  agree    : {ne_agree}")
print(f"  disagree : {ne_disagree}")
print()

# Net dollar impact of disagreements (sum of corrections)
if disagree:
    correction_sum = sum(
        float(r.get("settle_correction_usdc") or 0) for r in disagree
    )
    print(f"sum of settle_correction_usdc on early-resolved disagreements: ${correction_sum:.4f}")
