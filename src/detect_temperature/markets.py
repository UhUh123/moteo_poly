from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from .schema import MarketTarget

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def load_raw_markets(path: str | Path) -> list[dict]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list of markets in {path}")
    return data


def normalize_markets(raw_markets: Iterable[dict], include_unknown: bool = False) -> list[MarketTarget]:
    targets = [normalize_market(raw) for raw in raw_markets]
    if include_unknown:
        return targets
    return [
        target
        for target in targets
        if target.target_extreme in {"max", "min"} and target.target_date is not None
    ]


def normalize_market(raw: dict) -> MarketTarget:
    title = str(raw.get("title") or "")
    slug = str(raw.get("slug") or "")
    description = str(raw.get("description") or "")
    source_url = str(raw.get("resolution_source_url") or "")

    return MarketTarget(
        title=title,
        slug=slug,
        city=_parse_city(title),
        location_name=_parse_location_name(str(raw.get("location") or ""), description),
        target_date=_parse_target_date(title, slug, description),
        target_extreme=_parse_extreme(title),
        target_unit=_parse_unit(description),
        station_id=_parse_station_id(source_url, description),
        resolution_source_url=source_url,
        source_domain=_parse_domain(source_url),
        description=description,
    )


def write_targets_csv(targets: Iterable[MarketTarget], path: str | Path) -> None:
    records = [target.to_record() for target in targets]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        if not records:
            fh.write("")
            return
        writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def write_targets_jsonl(targets: Iterable[MarketTarget], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as fh:
        for target in targets:
            fh.write(json.dumps(target.to_record(), ensure_ascii=False) + "\n")


def read_targets_csv(path: str | Path) -> list[MarketTarget]:
    with Path(path).open("r", newline="", encoding="utf-8") as fh:
        return [MarketTarget.from_record(row) for row in csv.DictReader(fh)]


def _parse_extreme(title: str) -> str:
    lowered = title.lower()
    if "highest temperature" in lowered:
        return "max"
    if "lowest temperature" in lowered:
        return "min"
    return "unknown"


def _parse_city(title: str) -> str:
    match = re.search(r"(?:highest|lowest)\s+temperature\s+in\s+(.+?)\s+on\s+", title, re.I)
    if match:
        return match.group(1).strip()
    return ""


def _parse_location_name(raw_location: str, description: str) -> str:
    if "Hong Kong Observatory" in description:
        return "Hong Kong Observatory"
    return raw_location


def _parse_unit(description: str) -> str:
    targeted_patterns = [
        r"will resolve.*?\bin degrees\s+(Fahrenheit|Celsius)\s+on\b",
        r"recorded.*?\bin degrees\s+(Fahrenheit|Celsius)\s+on\b",
        r"measures temperatures (?:to [^.\n]+ )?\bin\s+(Fahrenheit|Celsius)\b",
        r"measures temperatures (?:to [^.\n]+ )?\bdegrees\s+(Fahrenheit|Celsius)\b",
    ]
    for pattern in targeted_patterns:
        match = re.search(pattern, description, re.I | re.S)
        if match:
            return match.group(1).lower()

    if re.search(r"degrees\s+Celsius|°C|ºC", description, re.I):
        return "celsius"
    if re.search(r"degrees\s+Fahrenheit|°F|ºF", description, re.I):
        return "fahrenheit"
    return "unknown"


def _parse_target_date(title: str, slug: str, description: str) -> date | None:
    description_match = re.search(
        r"\bon\s+(\d{1,2})\s+([A-Za-z]+)\s+'?(\d{2,4})\b",
        description,
        re.I,
    )
    if description_match:
        day, month_name, year_raw = description_match.groups()
        return _make_date(int(day), month_name, _normalize_year(year_raw))

    title_match = re.search(r"\bon\s+([A-Za-z]+)\s+(\d{1,2})\??", title, re.I)
    year = _parse_year_from_slug(slug)
    if title_match and year:
        month_name, day = title_match.groups()
        return _make_date(int(day), month_name, year)

    return None


def _make_date(day: int, month_name: str, year: int) -> date | None:
    month = MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _normalize_year(raw_year: str) -> int:
    year = int(raw_year)
    if year < 100:
        return 2000 + year
    return year


def _parse_year_from_slug(slug: str) -> int | None:
    match = re.search(r"(20\d{2})(?:$|[-_])", slug)
    if match:
        return int(match.group(1))
    return None


def _parse_station_id(source_url: str, description: str = "") -> str:
    if not source_url:
        return ""

    parsed = urlparse(source_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    for key in ("site", "station", "id", "ids"):
        for value in query.get(key, []):
            candidate = re.sub(r"[^A-Za-z0-9]", "", value).upper()
            if 3 <= len(candidate) <= 5:
                return candidate

    if "weather.gov.hk" in parsed.netloc.lower() and "Hong Kong Observatory" in description:
        return "HKO"

    if "wunderground.com" in parsed.netloc.lower() and path_parts:
        candidate = re.sub(r"[^A-Za-z0-9]", "", path_parts[-1]).upper()
        if 3 <= len(candidate) <= 5:
            return candidate

    combined = f"{parsed.path}?{parsed.query}".upper()
    match = re.search(r"\b([A-Z]{4})\b", combined)
    if match:
        return match.group(1)

    return ""


def _parse_domain(source_url: str) -> str:
    if not source_url:
        return ""
    return urlparse(source_url).netloc.lower().removeprefix("www.")
