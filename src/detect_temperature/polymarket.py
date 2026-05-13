from __future__ import annotations

import csv
import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

POLYMARKET_WEATHER_URL = "https://polymarket.com/weather"
POLYMARKET_GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
CLOB_BOOKS_URL = "https://clob.polymarket.com/books"
USER_AGENT = "detect-temperature/0.1"

# Retry policy for the public endpoints. polymarket.com sees occasional SSL
# EOFs / 5xx / timeouts from CDNs; CLOB is more stable but worth covering.
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_S = 2.0


def _request_with_retries(
    method: str,
    url: str,
    *,
    timeout: int,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    verify: bool = True,
    retries: int = DEFAULT_RETRIES,
    backoff_s: float = DEFAULT_BACKOFF_S,
) -> requests.Response:
    """GET/POST with retry on SSL/connection/5xx errors and exponential backoff.

    Raises the last exception only if every retry has failed.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
                verify=verify,
            )
            # Retry on transient server errors but not on 4xx (those are real)
            if 500 <= response.status_code < 600:
                last_exc = requests.HTTPError(
                    f"{response.status_code} server error", response=response
                )
            else:
                return response
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            last_exc = exc
        if attempt < retries:
            time.sleep(backoff_s * (2 ** attempt))
    if last_exc is None:
        last_exc = RuntimeError(f"unreachable retry path for {url}")
    raise last_exc


@dataclass(frozen=True)
class PolymarketWeatherMarket:
    event_id: str
    event_slug: str
    event_title: str
    event_volume: float | None
    market_id: str
    condition_id: str
    market_slug: str
    question: str
    group_item_title: str
    outcomes: list[str]
    outcome_prices: list[float | None]
    clob_token_ids: list[str]
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    market_volume: float | None
    liquidity: float | None
    neg_risk: bool
    active: bool
    closed: bool
    accepting_orders: bool
    end_date: str

    def to_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_slug": self.event_slug,
            "event_title": self.event_title,
            "event_volume": self.event_volume,
            "market_id": self.market_id,
            "condition_id": self.condition_id,
            "market_slug": self.market_slug,
            "question": self.question,
            "group_item_title": self.group_item_title,
            "outcomes": json.dumps(self.outcomes, ensure_ascii=False),
            "outcome_prices": json.dumps(self.outcome_prices, ensure_ascii=False),
            "clob_token_ids": json.dumps(self.clob_token_ids, ensure_ascii=False),
            "yes_price": self.outcome_prices[0] if self.outcome_prices else None,
            "no_price": self.outcome_prices[1] if len(self.outcome_prices) > 1 else None,
            "yes_token_id": self.clob_token_ids[0] if self.clob_token_ids else "",
            "no_token_id": self.clob_token_ids[1] if len(self.clob_token_ids) > 1 else "",
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread": self.spread,
            "market_volume": self.market_volume,
            "liquidity": self.liquidity,
            "neg_risk": int(self.neg_risk),
            "active": int(self.active),
            "closed": int(self.closed),
            "accepting_orders": int(self.accepting_orders),
            "end_date": self.end_date,
        }


class PolymarketWeatherClient:
    def __init__(
        self,
        weather_url: str = POLYMARKET_WEATHER_URL,
        geoblock_url: str = POLYMARKET_GEOBLOCK_URL,
        timeout_s: int = 30,
    ) -> None:
        self.weather_url = weather_url
        self.geoblock_url = geoblock_url
        self.timeout_s = timeout_s

    def fetch_weather_events(self) -> list[dict[str, Any]]:
        response = _request_with_retries(
            "GET",
            self.weather_url,
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return extract_weather_events_from_html(response.text)

    def fetch_geoblock(self) -> dict[str, Any]:
        response = _request_with_retries(
            "GET",
            self.geoblock_url,
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


class PolymarketClobClient:
    def __init__(
        self,
        books_url: str = CLOB_BOOKS_URL,
        timeout_s: int = 30,
        verify_tls: bool = True,
    ) -> None:
        self.books_url = books_url
        self.timeout_s = timeout_s
        self.verify_tls = verify_tls

    def fetch_order_books(self, token_ids: list[str], batch_size: int = 500) -> list[dict[str, Any]]:
        books: list[dict[str, Any]] = []
        unique_token_ids = [token_id for token_id in dict.fromkeys(token_ids) if token_id]
        for start in range(0, len(unique_token_ids), batch_size):
            batch = unique_token_ids[start:start + batch_size]
            response = _request_with_retries(
                "POST",
                self.books_url,
                json_body=[{"token_id": token_id} for token_id in batch],
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=self.timeout_s,
                verify=self.verify_tls,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                books.extend(book for book in payload if isinstance(book, dict))
        return books


def extract_weather_events_from_html(page_html: str) -> list[dict[str, Any]]:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
        page_html,
        re.S,
    )
    if not match:
        raise ValueError("Could not find __NEXT_DATA__ in Polymarket weather page")

    payload = json.loads(html.unescape(match.group(1)))
    queries = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("dehydratedState", {})
        .get("queries", [])
    )
    for query in queries:
        query_key = query.get("queryKey")
        data = query.get("state", {}).get("data")
        if query_key == ["weather", "markets"] and isinstance(data, list):
            return data

    candidate = _find_weather_event_list(payload)
    if candidate is None:
        raise ValueError("Could not find weather markets query data")
    return candidate


def flatten_temperature_markets(events: list[dict[str, Any]]) -> list[PolymarketWeatherMarket]:
    rows = []
    for event in events:
        title = str(event.get("title") or "")
        if "temperature" not in title.lower():
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            question = str(market.get("question") or "")
            if "temperature" not in question.lower():
                continue
            rows.append(
                PolymarketWeatherMarket(
                    event_id=str(event.get("id") or ""),
                    event_slug=str(event.get("slug") or ""),
                    event_title=title,
                    event_volume=_as_float(event.get("volume")),
                    market_id=str(market.get("id") or ""),
                    condition_id=str(market.get("conditionId") or ""),
                    market_slug=str(market.get("slug") or ""),
                    question=question,
                    group_item_title=str(market.get("groupItemTitle") or ""),
                    outcomes=[str(value) for value in market.get("outcomes") or []],
                    outcome_prices=[_as_float(value) for value in market.get("outcomePrices") or []],
                    clob_token_ids=[str(value) for value in market.get("clobTokenIds") or []],
                    best_bid=_as_float(market.get("bestBid")),
                    best_ask=_as_float(market.get("bestAsk")),
                    spread=_as_float(market.get("spread")),
                    market_volume=_as_float(market.get("volume")),
                    liquidity=_as_float(market.get("liquidity")),
                    neg_risk=bool(market.get("negRisk")),
                    active=bool(market.get("active")),
                    closed=bool(market.get("closed")),
                    accepting_orders=bool(market.get("acceptingOrders")),
                    end_date=str(market.get("endDate") or ""),
                )
            )
    return rows


def write_polymarket_markets_csv(markets: list[PolymarketWeatherMarket], path: str | Path) -> None:
    records = [market.to_record() for market in markets]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        if not records:
            fh.write("")
            return
        writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def write_json(payload: Any, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def token_ids_from_market_records(records: list[dict[str, Any]], include_no: bool = True) -> list[str]:
    token_ids = []
    for record in records:
        yes_token = str(record.get("yes_token_id") or "").strip()
        no_token = str(record.get("no_token_id") or "").strip()
        if yes_token:
            token_ids.append(yes_token)
        if include_no and no_token:
            token_ids.append(no_token)
    return list(dict.fromkeys(token_ids))


def _find_weather_event_list(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict):
        if _looks_like_weather_event_list(payload):
            return [payload]
        for value in payload.values():
            found = _find_weather_event_list(value)
            if found is not None:
                return found
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            weather_count = sum(
                1
                for item in payload
                if "temperature" in str(item.get("title") or "").lower()
                and isinstance(item.get("markets"), list)
            )
            if weather_count >= 3:
                return payload
        for value in payload:
            found = _find_weather_event_list(value)
            if found is not None:
                return found
    return None


def _looks_like_weather_event_list(payload: dict[str, Any]) -> bool:
    return "temperature" in str(payload.get("title") or "").lower() and isinstance(payload.get("markets"), list)


def _as_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
