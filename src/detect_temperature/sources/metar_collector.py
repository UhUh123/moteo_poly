"""METAR observation collector with deduplicated daily archives.

Why this exists
---------------
Chapter 6 of the learning guide is blunt: the resolve source for
Polymarket weather markets is METAR, the airport observation feed
that aviationweather.gov publishes for free. Our project so far
fetches METAR ad-hoc inside `near_close.py`, but never persists
anything. That means if we ever want to:

  - reconstruct what the temperature actually was at any past minute
    (for the chapter 6 §8 "edge close to resolve" strategy),
  - measure per-station bias by wind regime (chapter 6 §7),
  - cross-check our settle calculation against ground truth
    independent of weather.com,

...we cannot. The data went through us and was thrown away. Same
class of bug as `state_archive`: a feed that runs forever without
saving anything is a feed of sand.

What this module does
---------------------
One function, `collect_metar_snapshot(station_ids, history_root)`,
that:

1. Asks aviationweather.gov in ONE HTTP call for the latest METAR
   of every station in the list (bulk fetch).
2. Parses each report into a row with the fields we care about for
   trading: temp, dewpoint, wind, pressure, observed_at, raw_text.
3. Appends to `history_root/<YYYY-MM-DD>.csv` based on the
   observation's UTC date (so a 23:50 UTC report and a 00:10 UTC
   report end up in different daily files).
4. Deduplicates on `(station_id, observed_at_iso)`. Re-running the
   collector on the same minute is a no-op.
5. Atomically rewrites each daily file via `<file>.tmp` -> rename.

Cadence
-------
METAR usually cycles every 30 or 60 minutes per station, with
unscheduled SPECI reports in between when conditions change quickly.
Polling every 10 minutes is enough to catch every report once and
SPECIs within 10 minutes of issue. Faster is wasted bandwidth and
risks looking like an abuser to the gov endpoint.

Public, no-key endpoint
-----------------------
https://aviationweather.gov/api/data/metar?ids=...&format=json

Schema (selected fields used here):
  icaoId       : str — station ICAO
  obsTime      : int (epoch seconds UTC) — observation time
  reportTime   : str (ISO UTC) — when METAR was issued
  metarType    : "METAR" | "SPECI"
  temp         : float (C)
  dewp         : float (C)
  wdir         : int (degrees, 0=variable)
  wspd         : int (knots)
  altim        : float (hPa)
  visib        : str | float
  rawOb        : str — raw METAR text
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import requests


METAR_ENDPOINT = "https://aviationweather.gov/api/data/metar"
USER_AGENT = "detect-temperature/0.1 (METAR archive collector)"

# CSV column order is part of the on-disk contract. Adding columns is fine,
# changing order or removing them breaks downstream readers.
CSV_COLUMNS = [
    "station_id",
    "observed_at",       # ISO UTC string of the METAR's obsTime
    "report_type",       # "METAR" or "SPECI"
    "temp_c",
    "dewpoint_c",
    "wind_dir_deg",      # 0 means "variable" per ICAO METAR spec
    "wind_speed_kt",
    "altim_hpa",
    "visibility",        # str — METAR uses "10+", "1/2", "10000" etc.
    "raw_text",
    "fetched_at",        # when WE saved the row, helps debug late-arriving SPECIs
]

DEDUPE_KEY = ("station_id", "observed_at")


@dataclass(frozen=True)
class MetarRecord:
    station_id: str
    observed_at: datetime
    report_type: str
    temp_c: float | None
    dewpoint_c: float | None
    wind_dir_deg: int | None
    wind_speed_kt: int | None
    altim_hpa: float | None
    visibility: str
    raw_text: str
    fetched_at: datetime

    def to_csv_row(self) -> dict[str, str]:
        return {
            "station_id": self.station_id,
            "observed_at": self.observed_at.isoformat(timespec="seconds"),
            "report_type": self.report_type,
            "temp_c": "" if self.temp_c is None else str(self.temp_c),
            "dewpoint_c": "" if self.dewpoint_c is None else str(self.dewpoint_c),
            "wind_dir_deg": "" if self.wind_dir_deg is None else str(self.wind_dir_deg),
            "wind_speed_kt": "" if self.wind_speed_kt is None else str(self.wind_speed_kt),
            "altim_hpa": "" if self.altim_hpa is None else str(self.altim_hpa),
            "visibility": self.visibility,
            "raw_text": self.raw_text,
            "fetched_at": self.fetched_at.isoformat(timespec="seconds"),
        }


def collect_metar_snapshot(
    station_ids: Iterable[str],
    history_root: str | Path,
    *,
    endpoint: str = METAR_ENDPOINT,
    timeout_s: int = 30,
    now_utc: datetime | None = None,
    fetcher: "callable | None" = None,
) -> dict:
    """Fetch latest METAR for every station and append to daily archive.

    Returns a small summary dict for logging / health.json.
    `fetcher` is for tests — defaults to a real HTTP call.
    """
    station_list = sorted({(s or "").upper().strip() for s in station_ids if s})
    station_list = [s for s in station_list if s]
    if not station_list:
        return {"requested": 0, "received": 0, "appended": 0, "stations": []}

    fetched_at = now_utc or datetime.now(timezone.utc)
    fetch = fetcher or _default_fetcher(endpoint=endpoint, timeout_s=timeout_s)
    payload = fetch(station_list)

    records: list[MetarRecord] = []
    for item in payload or []:
        record = _record_from_payload(item, fetched_at=fetched_at)
        if record is not None:
            records.append(record)

    history_root = Path(history_root)
    appended_total = 0
    days_touched: set[str] = set()
    for day_iso, day_records in _group_by_day(records).items():
        appended_total += _append_records_for_day(history_root, day_iso, day_records)
        days_touched.add(day_iso)

    return {
        "requested": len(station_list),
        "received": len(records),
        "appended": appended_total,
        "stations": station_list,
        "days_touched": sorted(days_touched),
        "endpoint": endpoint,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
    }


def _default_fetcher(*, endpoint: str, timeout_s: int):
    def fetch(station_list: list[str]) -> list[dict]:
        # The endpoint accepts comma-separated ids. 51 stations fits in a
        # single GET URL well under any sensible URL limit.
        response = requests.get(
            endpoint,
            params={"ids": ",".join(station_list), "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_s,
        )
        if response.status_code == 204:
            return []
        response.raise_for_status()
        try:
            data = response.json()
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            # API has been seen wrapping in {"data": [...]} historically
            for key in ("data", "items", "metars"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data if isinstance(data, list) else []
    return fetch


def _record_from_payload(item: dict, *, fetched_at: datetime) -> MetarRecord | None:
    if not isinstance(item, dict):
        return None
    station_id = _first_str(item, "icaoId", "stationId", "icao")
    if not station_id:
        return None
    observed_at = _parse_observed_at(item)
    if observed_at is None:
        # Without a timestamp the row is ambiguous on retries — drop it
        return None
    report_type_raw = _first_str(item, "metarType") or "METAR"
    return MetarRecord(
        station_id=station_id.upper(),
        observed_at=observed_at,
        report_type=report_type_raw.upper(),
        temp_c=_as_float(_first(item, "temp", "temp_c")),
        dewpoint_c=_as_float(_first(item, "dewp", "dewpoint", "dewpoint_c")),
        wind_dir_deg=_as_int(_first(item, "wdir", "windDirection")),
        wind_speed_kt=_as_int(_first(item, "wspd", "windSpeed")),
        altim_hpa=_as_float(_first(item, "altim", "altimeter", "slp")),
        visibility=_first_str(item, "visib", "visibility") or "",
        raw_text=_first_str(item, "rawOb", "rawText") or "",
        fetched_at=fetched_at,
    )


def _group_by_day(records: list[MetarRecord]) -> dict[str, list[MetarRecord]]:
    grouped: dict[str, list[MetarRecord]] = {}
    for r in records:
        day = r.observed_at.astimezone(timezone.utc).date().isoformat()
        grouped.setdefault(day, []).append(r)
    return grouped


def _append_records_for_day(
    history_root: Path, day_iso: str, new_records: list[MetarRecord]
) -> int:
    """Merge `new_records` into history_root/<day>.csv with dedupe.

    Returns the number of rows that were genuinely new on disk.
    """
    history_root.mkdir(parents=True, exist_ok=True)
    target = history_root / f"{day_iso}.csv"
    existing_keys: set[tuple[str, str]] = set()
    existing_rows: list[dict[str, str]] = []
    if target.exists():
        try:
            with target.open("r", newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    existing_rows.append(row)
                    existing_keys.add((row.get("station_id", ""), row.get("observed_at", "")))
        except Exception:
            # Corrupt or partially-written file: keep it as-is on disk
            # under a `.broken` suffix and start a fresh one. This is a
            # safety net so a glitch on Windows can't silently throw
            # away a day's worth of observations.
            backup = target.with_suffix(target.suffix + ".broken")
            target.replace(backup)
            existing_keys = set()
            existing_rows = []

    appended = 0
    rows_to_write = list(existing_rows)
    for record in new_records:
        row = record.to_csv_row()
        key = (row["station_id"], row["observed_at"])
        if key in existing_keys:
            continue
        existing_keys.add(key)
        rows_to_write.append(row)
        appended += 1

    if appended == 0 and target.exists():
        return 0

    tmp_path = target.with_suffix(target.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows_to_write:
            writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})
    os.replace(tmp_path, target)
    return appended


def load_station_ids(stations_path: str | Path) -> list[str]:
    """Read the canonical station inventory (training_stations.json)."""
    path = Path(stations_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    ids: list[str] = []
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                sid = entry.get("id") or entry.get("station_id") or entry.get("icao")
                if sid:
                    ids.append(str(sid).upper().strip())
    return [s for s in ids if s]


# ---- helpers ----------------------------------------------------------------


def _first(item: dict, *keys: str):
    for key in keys:
        if key in item and item[key] not in {"", None}:
            return item[key]
    return None


def _first_str(item: dict, *keys: str) -> str:
    value = _first(item, *keys)
    return "" if value is None else str(value)


def _as_float(value) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    f = _as_float(value)
    return None if f is None else int(round(f))


def _parse_observed_at(item: dict) -> datetime | None:
    """obsTime is the authoritative observation time. It may be epoch or
    ISO depending on which version of the API responds."""
    raw = _first(item, "obsTime", "observation_time", "reportTime", "receiptTime")
    if raw is None:
        return None
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    text = str(raw).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
