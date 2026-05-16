from __future__ import annotations

import json

import pytest

from detect_temperature.polymarket_resolution import (
    MarketResolution,
    fetch_event_resolution,
    parse_events_response,
    settle_agreement,
)


# ---------- live-schema fixture (verified 2026-05-15 against gamma API) ------


def _resolved_yes_payload(market_slug: str = "highest-temperature-in-amsterdam-on-may-12-2026-12c"):
    return [{
        "slug": "highest-temperature-in-amsterdam-on-may-12-2026",
        "markets": [{
            "slug": market_slug,
            "closed": True,
            "outcomePrices": '["1", "0"]',
            "umaResolutionStatus": "resolved",
            "resolutionSource": "https://www.wunderground.com/history/daily/nl/schiphol/EHAM",
        }],
    }]


def _resolved_no_payload():
    return [{
        "slug": "highest-temperature-in-amsterdam-on-may-12-2026",
        "markets": [{
            "slug": "highest-temperature-in-amsterdam-on-may-12-2026-13c",
            "closed": True,
            "outcomePrices": '["0", "1"]',
            "umaResolutionStatus": "resolved",
            "resolutionSource": "https://www.wunderground.com/...",
        }],
    }]


def _in_progress_payload():
    """closed=True can show up briefly with mid prices while UMA is still
    proposing. Should NOT be treated as resolved."""
    return [{
        "slug": "highest-temperature-in-amsterdam-on-may-12-2026",
        "markets": [{
            "slug": "highest-temperature-in-amsterdam-on-may-12-2026-12c",
            "closed": True,
            "outcomePrices": '["0.5", "0.5"]',
            "umaResolutionStatus": "proposed",
        }],
    }]


def _open_market_payload():
    return [{
        "slug": "highest-temperature-in-tokyo-on-may-20-2026",
        "markets": [{
            "slug": "highest-temperature-in-tokyo-on-may-20-2026-25c",
            "closed": False,
            "outcomePrices": '["0.42", "0.58"]',
            "umaResolutionStatus": "",
        }],
    }]


# ---------- parse_events_response: happy paths ------------------------------


def test_parse_yes_won() -> None:
    out = parse_events_response(_resolved_yes_payload())
    assert "highest-temperature-in-amsterdam-on-may-12-2026-12c" in out
    r = out["highest-temperature-in-amsterdam-on-may-12-2026-12c"]
    assert r.closed is True
    assert r.yes_outcome_price == 1.0
    assert r.no_outcome_price == 0.0
    assert r.outcome_prices_raw == '["1", "0"]'
    assert r.uma_status == "resolved"
    assert "wunderground" in r.resolution_source
    assert r.is_resolved is True
    assert r.yes_won is True


def test_parse_no_won() -> None:
    out = parse_events_response(_resolved_no_payload())
    r = next(iter(out.values()))
    assert r.is_resolved is True
    assert r.yes_won is False
    assert r.no_outcome_price == 1.0


def test_parse_in_progress_market_is_not_resolved() -> None:
    """closed=True but outcome prices haven't pinned to 0/1 yet must NOT
    be treated as resolved. Otherwise we'd record a false agree/disagree."""
    out = parse_events_response(_in_progress_payload())
    r = next(iter(out.values()))
    assert r.closed is True   # raw flag preserved for debugging
    assert r.is_resolved is False
    assert r.yes_won is None


def test_parse_open_market_is_not_resolved() -> None:
    out = parse_events_response(_open_market_payload())
    r = next(iter(out.values()))
    assert r.closed is False
    assert r.is_resolved is False
    assert r.yes_won is None


# ---------- outcomePrices format variants -----------------------------------


def test_parse_outcome_prices_already_decoded_list() -> None:
    """gamma occasionally returns the field as a real list, not a JSON string."""
    payload = [{
        "slug": "ev",
        "markets": [{"slug": "m1", "closed": True, "outcomePrices": ["1", "0"]}],
    }]
    r = parse_events_response(payload)["m1"]
    assert r.is_resolved
    assert r.yes_won is True


def test_parse_outcome_prices_floats() -> None:
    payload = [{
        "slug": "ev",
        "markets": [{"slug": "m1", "closed": True, "outcomePrices": [1.0, 0.0]}],
    }]
    r = parse_events_response(payload)["m1"]
    assert r.is_resolved
    assert r.yes_won is True


def test_parse_outcome_prices_missing() -> None:
    payload = [{
        "slug": "ev",
        "markets": [{"slug": "m1", "closed": True, "outcomePrices": None}],
    }]
    r = parse_events_response(payload)["m1"]
    assert r.is_resolved is False
    assert r.yes_outcome_price is None


def test_parse_outcome_prices_malformed_string() -> None:
    payload = [{
        "slug": "ev",
        "markets": [{"slug": "m1", "closed": True, "outcomePrices": "{ not json"}],
    }]
    r = parse_events_response(payload)["m1"]
    assert r.is_resolved is False
    assert r.outcome_prices_raw == "{ not json"


# ---------- response-shape defensive parsing --------------------------------


def test_parse_empty_list() -> None:
    assert parse_events_response([]) == {}


def test_parse_event_without_markets() -> None:
    assert parse_events_response([{"slug": "ev", "title": "x"}]) == {}


def test_parse_data_wrapper_object() -> None:
    """gamma sometimes wraps as {"data": [...]}; we should unwrap it."""
    payload = {"data": _resolved_yes_payload()}
    out = parse_events_response(payload)
    assert len(out) == 1


def test_parse_garbage_input() -> None:
    assert parse_events_response("nonsense") == {}
    assert parse_events_response(None) == {}
    assert parse_events_response(42) == {}


def test_parse_market_slug_required() -> None:
    """Markets without a slug are skipped, not crashed on."""
    payload = [{
        "slug": "ev",
        "markets": [
            {"closed": True, "outcomePrices": '["1","0"]'},  # no slug
            {"slug": "good-slug", "closed": True, "outcomePrices": '["1","0"]'},
        ],
    }]
    out = parse_events_response(payload)
    assert list(out.keys()) == ["good-slug"]


# ---------- fetch_event_resolution wiring -----------------------------------


def test_fetch_event_resolution_uses_supplied_fetcher() -> None:
    captured = []

    def fake(slug: str):
        captured.append(slug)
        return _resolved_yes_payload()

    out = fetch_event_resolution("highest-temperature-in-amsterdam-on-may-12-2026", fetcher=fake)
    assert captured == ["highest-temperature-in-amsterdam-on-may-12-2026"]
    assert len(out) == 1


def test_fetch_event_resolution_empty_slug_short_circuits() -> None:
    called = []

    def fake(_):
        called.append(1)
        return []

    out = fetch_event_resolution("", fetcher=fake)
    assert out == {}
    assert called == []   # don't touch the network on empty input


# ---------- settle_agreement -----------------------------------------------


def test_settle_agreement_agree_yes_won() -> None:
    res = parse_events_response(_resolved_yes_payload())["highest-temperature-in-amsterdam-on-may-12-2026-12c"]
    assert settle_agreement(our_yes_won=True, polymarket_resolution=res) == "agree"


def test_settle_agreement_disagree_yes_no() -> None:
    res = parse_events_response(_resolved_yes_payload())["highest-temperature-in-amsterdam-on-may-12-2026-12c"]
    # Polymarket says YES won, we say NO
    assert settle_agreement(our_yes_won=False, polymarket_resolution=res) == "disagree"


def test_settle_agreement_pending_when_not_resolved() -> None:
    res = parse_events_response(_in_progress_payload())["highest-temperature-in-amsterdam-on-may-12-2026-12c"]
    assert settle_agreement(our_yes_won=True, polymarket_resolution=res) == "pending"


def test_settle_agreement_no_data() -> None:
    assert settle_agreement(our_yes_won=True, polymarket_resolution=None) == "no_data"


# ---------- MarketResolution invariants -------------------------------------


def test_resolution_requires_pinned_endpoints_for_is_resolved() -> None:
    """Strict 0/1 check: a 0.99/0.01 market is NOT yet a final answer.
    Without this guard we'd commit to an agree/disagree on flickering
    pre-resolution numbers."""
    r = MarketResolution(
        market_slug="m1",
        closed=True,
        yes_outcome_price=0.99,
        no_outcome_price=0.01,
        outcome_prices_raw='["0.99","0.01"]',
        uma_status="proposed",
        resolution_source="",
    )
    assert r.is_resolved is False
    assert r.yes_won is None


def test_resolution_requires_closed_flag() -> None:
    """Even with prices pinned, an open market shouldn't count as resolved."""
    r = MarketResolution(
        market_slug="m1",
        closed=False,
        yes_outcome_price=1.0,
        no_outcome_price=0.0,
        outcome_prices_raw='["1","0"]',
        uma_status="resolved",
        resolution_source="",
    )
    assert r.is_resolved is False
