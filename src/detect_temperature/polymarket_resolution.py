"""Independent settle cross-check via the Polymarket gamma API.

Why this exists
---------------
Our `paper._settle_position` decides won/lost by reading
`data/actuals.csv` (Wunderground/HKO/Synoptic) and comparing the
observed temperature to the bucket interval we parsed from the
question text. That is OUR opinion of what should have happened.

Polymarket itself resolves through UMA on-chain. They may disagree
in three ways:
  1. Different resolve station from the one we used (NYC: KLGA vs
     KJFK vs Central Park; Sao Paulo: SBGR vs SBSP).
  2. Different rounding rule on borderline temperatures.
  3. UMA dispute / manual override.

Without independent cross-check we will silently bank "wins" that
the actual market resolved as losses, and our 79% reported win-rate
becomes unreliable.

What this module does
---------------------
One function, `fetch_event_resolution(event_slug)`, that:

1. Calls https://gamma-api.polymarket.com/events?slug=<event_slug>
   (key-less, public).
2. Parses the response into one MarketResolution per market_slug
   inside the event.
3. Reports closed / outcome prices / UMA status / resolution_source.

It does NOT decide our PnL. The caller (paper._settle_position)
keeps using actuals.csv as the source of truth for paper money,
records Polymarket's verdict alongside, and flags an "agree" /
"disagree" / "pending" string so we can later audit how often the
two diverge.

Schema (verified live 2026-05-15 for resolved Amsterdam market):
  events?slug=highest-temperature-in-amsterdam-on-may-12-2026
  -> [{ markets: [
       {
         "slug": "highest-temperature-in-amsterdam-on-may-12-2026-12c",
         "closed": true,
         "outcomePrices": ["0", "1"],   # YES_price, NO_price
         "umaResolutionStatus": "resolved",
         "resolutionSource": "https://www.wunderground.com/.../EHAM",
         ...
       },
       ...
     ]}]
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import requests


GAMMA_EVENTS_ENDPOINT = "https://gamma-api.polymarket.com/events"
USER_AGENT = "detect-temperature/0.1 (settle cross-check)"


@dataclass(frozen=True)
class MarketResolution:
    market_slug: str
    closed: bool
    # Reported by Polymarket as a 2-element list ["yes_price", "no_price"]
    # after resolution becomes "1"/"0" (or "0"/"1" if NO won). Kept as
    # floats here for arithmetic, plus a raw string for audit trails.
    yes_outcome_price: float | None
    no_outcome_price: float | None
    outcome_prices_raw: str
    # uma_status is "resolved" | "proposed" | "disputed" | "" (unknown).
    uma_status: str
    resolution_source: str

    @property
    def is_resolved(self) -> bool:
        """True only when the market is fully closed and outcome prices
        are pinned to 0 and 1 in some order. We are deliberately strict:
        a "0.999/0.001" market is not yet a final answer."""
        if not self.closed:
            return False
        if self.yes_outcome_price is None or self.no_outcome_price is None:
            return False
        # Both must be exactly 0 or 1, summing to 1
        valid_pair = {self.yes_outcome_price, self.no_outcome_price} == {0.0, 1.0}
        return valid_pair

    @property
    def yes_won(self) -> bool | None:
        """None until is_resolved; otherwise True if YES_price == 1."""
        if not self.is_resolved:
            return None
        return self.yes_outcome_price == 1.0


Fetcher = Callable[[str], dict | list]


def _default_fetcher(timeout_s: int = 15) -> Fetcher:
    def fetch(event_slug: str):
        response = requests.get(
            GAMMA_EVENTS_ENDPOINT,
            params={"slug": event_slug},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_s,
        )
        response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError:
            return []
    return fetch


def fetch_event_resolution(
    event_slug: str,
    fetcher: Fetcher | None = None,
) -> dict[str, MarketResolution]:
    """Return market_slug -> MarketResolution for every market inside
    `event_slug`. Empty dict if the event is not found or the response
    is malformed (network errors propagate to the caller as exceptions
    so it can decide whether to retry / skip).
    """
    if not event_slug:
        return {}
    fetch = fetcher or _default_fetcher()
    payload = fetch(event_slug)
    return parse_events_response(payload)


def parse_events_response(payload: Any) -> dict[str, MarketResolution]:
    """Pure-function parser, no I/O. Public so tests can hit it without
    monkeypatching requests."""
    if isinstance(payload, dict):
        # gamma sometimes returns {"data": [...]}. Handle defensively.
        for key in ("data", "events"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            return {}
    if not isinstance(payload, list):
        return {}

    out: dict[str, MarketResolution] = {}
    for event in payload:
        if not isinstance(event, dict):
            continue
        markets = event.get("markets") or []
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            slug = str(market.get("slug") or "").strip()
            if not slug:
                continue
            yes, no, raw = _parse_outcome_prices(market.get("outcomePrices"))
            out[slug] = MarketResolution(
                market_slug=slug,
                closed=bool(market.get("closed")),
                yes_outcome_price=yes,
                no_outcome_price=no,
                outcome_prices_raw=raw,
                uma_status=str(market.get("umaResolutionStatus") or "").strip(),
                resolution_source=str(market.get("resolutionSource") or "").strip(),
            )
    return out


def _parse_outcome_prices(raw_value: Any) -> tuple[float | None, float | None, str]:
    """outcomePrices arrives either as a JSON string '["0", "1"]' or
    already-decoded list ["0", "1"]. Convention: index 0 = YES, 1 = NO."""
    if raw_value in (None, ""):
        return None, None, ""
    raw_str = raw_value if isinstance(raw_value, str) else json.dumps(raw_value)
    parsed: Any = raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return None, None, raw_str
    if not isinstance(parsed, (list, tuple)) or len(parsed) < 2:
        return None, None, raw_str
    yes = _to_float(parsed[0])
    no = _to_float(parsed[1])
    return yes, no, raw_str


def _to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def settle_agreement(
    our_yes_won: bool,
    polymarket_resolution: MarketResolution | None,
) -> str:
    """Three-way string: 'agree', 'disagree', 'pending', 'no_data'.

    We store this on each settled paper position so we can later audit
    how often our settle disagreed with the on-chain resolution.
    """
    if polymarket_resolution is None:
        return "no_data"
    if not polymarket_resolution.is_resolved:
        return "pending"
    return "agree" if polymarket_resolution.yes_won == our_yes_won else "disagree"
