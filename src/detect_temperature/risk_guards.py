from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DrawdownAbort(RuntimeError):
    """Raised when realized drawdown breaches the configured abort threshold."""


def check_drawdown(
    state_paths: list[str | Path],
    abort_usdc: float,
) -> dict[str, Any]:
    """Inspect the latest paper portfolio state for realized drawdown breaches.

    `abort_usdc` is expressed as a negative floor (e.g. -10.0 for a $10 loss cap).
    Returns a dict with inspected state, whether the cap was breached, and the
    realized PnL observed. Raises DrawdownAbort when breach is detected.
    """
    realized = 0.0
    inspected: str | None = None
    for raw in state_paths:
        path = Path(raw)
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            continue
        value = summary.get("realized_pnl_usdc")
        if value in {None, ""}:
            continue
        try:
            realized = float(value)
        except (TypeError, ValueError):
            continue
        inspected = str(path)
        break

    breached = realized <= abort_usdc
    result = {
        "inspected_state": inspected,
        "realized_pnl_usdc": round(realized, 4),
        "abort_usdc": round(abort_usdc, 4),
        "breached": breached,
    }
    if breached:
        raise DrawdownAbort(
            "drawdown kill-switch engaged: realized_pnl_usdc="
            f"{realized:.4f} <= abort_usdc={abort_usdc:.4f} "
            f"(source={inspected or 'no state found'})"
        )
    return result
