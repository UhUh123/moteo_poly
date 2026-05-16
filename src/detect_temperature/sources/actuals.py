from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from detect_temperature.schema import MarketTarget
from detect_temperature.sources.base import StationMetadata
from detect_temperature.units import celsius_to_fahrenheit, normalize_temperature

WEATHER_COM_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
WEATHER_COM_HISTORY_ENDPOINT = "https://api.weather.com/v1/location"
HKO_DAILY_EXTRACT_ENDPOINT = "https://www.hko.gov.hk/cis/dailyExtract"
SYNOPTIC_TIMESERIES_ENDPOINT = "https://api.synopticdata.com/v2/stations/timeseries"
WEATHER_GOV_API_KEY_JS = "https://www.weather.gov/source/wrh/apiKey.js"
USER_AGENT = "detect-temperature/0.1"

# Default histroy root for METAR archive collected by `metar_collector`.
DEFAULT_METAR_HISTORY_ROOT = Path("data/metar_history")
# Below this many METAR samples we refuse to declare a daily max/min.
# 12 ≈ half a day at the standard 60 min METAR cadence; below that we
# don't know if we missed the actual peak / trough.
METAR_MIN_SAMPLES_FOR_DAILY_EXTREME = 12


@dataclass(frozen=True)
class ActualTemperature:
    slug: str
    station_id: str
    target_date: date | None
    target_extreme: str
    resolution_unit: str
    observed_temp_c: float | None
    observed_temp_f: float | None
    observed_resolution_value: float | None
    provider: str
    status: str
    sample_count: int = 0
    source_url: str = ""
    notes: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "station_id": self.station_id,
            "target_date": self.target_date.isoformat() if self.target_date else "",
            "target_extreme": self.target_extreme,
            "resolution_unit": self.resolution_unit,
            "observed_temp_c": self.observed_temp_c,
            "observed_temp_f": self.observed_temp_f,
            "observed_resolution_value": self.observed_resolution_value,
            "provider": self.provider,
            "status": self.status,
            "sample_count": self.sample_count,
            "source_url": self.source_url,
            "notes": self.notes,
        }


class WeatherComHistoricalActualsProvider:
    provider = "weather_com_historical"

    def __init__(
        self,
        api_key: str = WEATHER_COM_API_KEY,
        endpoint: str = WEATHER_COM_HISTORY_ENDPOINT,
        timeout_s: int = 30,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s

    def collect(self, target: MarketTarget, station: StationMetadata | None) -> ActualTemperature:
        if station is None or not station.country:
            return _pending(target, self.provider, "missing station country")
        if target.target_date is None:
            return _pending(target, self.provider, "missing target date")

        unit = "e" if target.target_unit == "fahrenheit" else "m"
        country = station.country.upper()
        location_key = f"{target.station_id}:9:{country}"
        url = f"{self.endpoint}/{location_key}/observations/historical.json"
        params = {
            "apiKey": self.api_key,
            "units": unit,
            "startDate": target.target_date.strftime("%Y%m%d"),
            "endDate": target.target_date.strftime("%Y%m%d"),
        }
        response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=self.timeout_s)
        if response.status_code == 204:
            return _pending(target, self.provider, "weather.com returned no historical data", _redacted_url(url, params))
        response.raise_for_status()
        payload = response.json()
        observations = payload.get("observations") or []
        temps = [_as_float(item.get("temp")) for item in observations if isinstance(item, dict)]
        temps = [temp for temp in temps if temp is not None]
        if not temps:
            return _pending(target, self.provider, "no temperature samples", _redacted_url(url, params))

        resolution_value = max(temps) if target.target_extreme == "max" else min(temps)
        observed_c = normalize_temperature(resolution_value, target.target_unit)
        return _actual(
            target=target,
            provider=self.provider,
            observed_temp_c=observed_c,
            observed_resolution_value=resolution_value,
            sample_count=len(temps),
            source_url=_redacted_url(url, params),
        )


class HkoDailyExtractActualsProvider:
    provider = "hko_daily_extract"

    def __init__(self, endpoint: str = HKO_DAILY_EXTRACT_ENDPOINT, timeout_s: int = 30) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s

    def collect(self, target: MarketTarget, station: StationMetadata | None = None) -> ActualTemperature:
        if target.target_date is None:
            return _pending(target, self.provider, "missing target date")
        url = f"{self.endpoint}/dailyExtract_{target.target_date:%Y%m}.xml"
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout_s)
        response.raise_for_status()
        payload = response.json()
        day = f"{target.target_date.day:02d}"
        month = target.target_date.month
        for month_data in payload.get("stn", {}).get("data", []):
            if int(month_data.get("month", -1)) != month:
                continue
            for row in month_data.get("dayData", []):
                if not row or str(row[0]) != day:
                    continue
                index = 2 if target.target_extreme == "max" else 4
                value = _as_float(row[index] if len(row) > index else None)
                if value is None:
                    return _pending(target, self.provider, "daily extract row has no target value", url)
                return _actual(
                    target=target,
                    provider=self.provider,
                    observed_temp_c=value,
                    observed_resolution_value=value,
                    sample_count=1,
                    source_url=url,
                )
        return _pending(target, self.provider, "target day is not present in HKO extract", url)


class SynopticTimeseriesActualsProvider:
    provider = "synoptic_timeseries"

    def __init__(
        self,
        endpoint: str = SYNOPTIC_TIMESERIES_ENDPOINT,
        api_key_js_url: str = WEATHER_GOV_API_KEY_JS,
        timeout_s: int = 30,
    ) -> None:
        self.endpoint = endpoint
        self.api_key_js_url = api_key_js_url
        self.timeout_s = timeout_s
        self._token: str | None = None

    def collect(self, target: MarketTarget, station: StationMetadata | None = None) -> ActualTemperature:
        if target.target_date is None:
            return _pending(target, self.provider, "missing target date")
        token = self._get_token()
        params = {
            "STID": target.station_id,
            "showemptystations": "1",
            "start": f"{target.target_date:%Y%m%d}0000",
            "end": f"{target.target_date:%Y%m%d}2359",
            "complete": "1",
            "token": token,
            "obtimezone": "local",
        }
        response = requests.get(
            self.endpoint,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Origin": "https://www.weather.gov",
                "Referer": f"https://www.weather.gov/wrh/timeseries?site={target.station_id}",
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        summary = payload.get("SUMMARY") or {}
        message = str(summary.get("RESPONSE_MESSAGE") or "")
        if summary.get("RESPONSE_CODE") != 1:
            return _pending(target, self.provider, message or "synoptic returned non-ok response", _redacted_url(self.endpoint, params))

        stations = payload.get("STATION") or []
        observations = (stations[0].get("OBSERVATIONS") if stations else {}) or {}
        temps = _extract_synoptic_temperatures(observations)
        if not temps:
            return _pending(target, self.provider, "no air_temp samples", _redacted_url(self.endpoint, params))
        observed_c = max(temps) if target.target_extreme == "max" else min(temps)
        resolution_value = celsius_to_fahrenheit(observed_c) if target.target_unit == "fahrenheit" else observed_c
        return _actual(
            target=target,
            provider=self.provider,
            observed_temp_c=observed_c,
            observed_resolution_value=resolution_value,
            sample_count=len(temps),
            source_url=_redacted_url(self.endpoint, params),
        )

    def _get_token(self) -> str:
        if self._token:
            return self._token
        response = requests.get(self.api_key_js_url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout_s)
        response.raise_for_status()
        match = re.search(r"mesoToken\s*=\s*['\"]([^'\"]+)['\"]", response.text)
        if not match:
            raise ValueError("Could not parse weather.gov Synoptic token")
        self._token = match.group(1)
        return self._token


class MetarHistoryActualsProvider:
    """Local-disk fallback that aggregates METAR observations into a
    daily max/min for a station-day.

    Why this exists
    ---------------
    `weather.com` returns 403 for non-US ICAO codes (RKSI, ZSPD, LLBG,
    WSSS, ...) under our public apiKey. Synoptic timeseries occasionally
    rate-limits weather.gov. Both failure modes silently keep paper
    positions in `error` status and stall settle. But the
    `metar_collector` task already persists every report from the same
    51 ICAO stations into `data/metar_history/<UTC-day>.csv`. Re-using
    that archive lets us settle the affected positions without making
    any new network call.

    Caveats
    -------
    - Polymarket markets resolve in the station's LOCAL day (e.g. "May
      12 in Shanghai" = 00:00..23:59 local Shanghai time, not UTC).
      We approximate the offset from longitude (lon/15 hours), which is
      accurate to within ~30 min near timezone borders. Good enough for
      a daily extreme.
    - We require at least METAR_MIN_SAMPLES_FOR_DAILY_EXTREME reports
      inside the window before declaring `ok`. Below that we return
      `pending` rather than risk reporting a false max/min from a half-
      sampled day.
    - If the history root is empty (e.g. running on the dev mac) we
      degrade silently: provider returns `pending`, primary error stays
      visible to the user.
    """

    provider = "metar_history_archive"

    def __init__(
        self,
        history_root: str | Path = DEFAULT_METAR_HISTORY_ROOT,
        min_samples: int = METAR_MIN_SAMPLES_FOR_DAILY_EXTREME,
    ) -> None:
        self.history_root = Path(history_root)
        self.min_samples = min_samples

    def collect(
        self, target: MarketTarget, station: StationMetadata | None
    ) -> ActualTemperature:
        if target.target_date is None:
            return _pending(target, self.provider, "missing target date")
        if not target.station_id:
            return _pending(target, self.provider, "missing station id")
        if not self.history_root.exists():
            return _pending(target, self.provider, f"metar history root missing: {self.history_root}")

        offset_hours = _station_local_offset_hours(station)
        window = _local_day_utc_window(target.target_date, offset_hours)
        if window is None:
            return _pending(target, self.provider, "invalid local-day window")

        start_utc, end_utc = window
        samples = list(
            _iter_metar_temps_in_window(
                history_root=self.history_root,
                station_id=target.station_id.upper(),
                start_utc=start_utc,
                end_utc_exclusive=end_utc,
            )
        )
        if not samples:
            return _pending(
                target,
                self.provider,
                f"no metar samples for {target.station_id} on {target.target_date.isoformat()}",
                source_url=str(self.history_root.resolve()),
            )
        if len(samples) < self.min_samples:
            return _pending(
                target,
                self.provider,
                f"only {len(samples)} metar sample(s) (<{self.min_samples}) "
                f"for {target.station_id} on {target.target_date.isoformat()}",
                source_url=str(self.history_root.resolve()),
            )

        observed_c = max(samples) if target.target_extreme == "max" else min(samples)
        resolution_value = (
            celsius_to_fahrenheit(observed_c)
            if target.target_unit == "fahrenheit"
            else observed_c
        )
        return _actual(
            target=target,
            provider=self.provider,
            observed_temp_c=observed_c,
            observed_resolution_value=resolution_value,
            sample_count=len(samples),
            source_url=str(self.history_root.resolve()),
        )


def collect_actual_for_target(
    target: MarketTarget,
    station: StationMetadata | None,
    today: date | None = None,
    finalization_lag_days: int = 1,
    metar_history_root: str | Path | None = DEFAULT_METAR_HISTORY_ROOT,
) -> ActualTemperature:
    """Collect resolved temperature for a Polymarket weather target.

    Tries the primary provider for `target.source_domain` first
    (Wunderground / HKO / Synoptic). If that returns `error` (e.g.
    `weather.com` 403 for non-US ICAO codes, or Synoptic rate-limit),
    falls back to `MetarHistoryActualsProvider` which aggregates the
    local METAR archive into a daily max/min. The fallback is skipped
    when:

      - `metar_history_root` is None (caller opted out), or
      - the archive directory does not exist (dev mac), or
      - the primary already returned `ok` or `pending` (we never
        downgrade `pending` — that's the finalization-lag wait state).
    """
    if target.target_date is None:
        return _pending(target, "none", "missing target date")
    if not _is_finalized_enough(target.target_date, today=today, lag_days=finalization_lag_days):
        return _pending(target, "none", f"waiting for finalization lag of {finalization_lag_days} day(s)")

    primary = _collect_primary(target, station)
    if primary.status != "error" or metar_history_root is None:
        return primary

    fallback = MetarHistoryActualsProvider(history_root=metar_history_root).collect(
        target, station
    )
    if fallback.status == "ok":
        # Carry the primary's failure note for audit, prefixed.
        primary_note = (primary.notes or "").strip()
        fallback_note = (fallback.notes or "").strip()
        merged_note = (
            f"primary {primary.provider or 'unknown'} failed: {primary_note}; "
            f"fell back to {fallback.provider}"
            if primary_note
            else f"fell back to {fallback.provider}"
        )
        if fallback_note:
            merged_note = f"{merged_note}; {fallback_note}"
        return ActualTemperature(
            slug=fallback.slug,
            station_id=fallback.station_id,
            target_date=fallback.target_date,
            target_extreme=fallback.target_extreme,
            resolution_unit=fallback.resolution_unit,
            observed_temp_c=fallback.observed_temp_c,
            observed_temp_f=fallback.observed_temp_f,
            observed_resolution_value=fallback.observed_resolution_value,
            provider=fallback.provider,
            status="ok",
            sample_count=fallback.sample_count,
            source_url=fallback.source_url,
            notes=merged_note,
        )
    # Fallback also failed: keep the original error so the user sees
    # the real upstream cause. Append a hint that fallback was tried.
    fallback_note = (fallback.notes or "").strip()
    suffix = f" | metar_fallback={fallback.status}"
    if fallback_note:
        suffix += f" ({fallback_note})"
    return ActualTemperature(
        slug=primary.slug,
        station_id=primary.station_id,
        target_date=primary.target_date,
        target_extreme=primary.target_extreme,
        resolution_unit=primary.resolution_unit,
        observed_temp_c=primary.observed_temp_c,
        observed_temp_f=primary.observed_temp_f,
        observed_resolution_value=primary.observed_resolution_value,
        provider=primary.provider,
        status=primary.status,
        sample_count=primary.sample_count,
        source_url=primary.source_url,
        notes=(primary.notes or "") + suffix,
    )


def _collect_primary(
    target: MarketTarget, station: StationMetadata | None
) -> ActualTemperature:
    """Single-provider dispatch by source_domain. Catches network /
    HTTP exceptions and surfaces them as ActualTemperature(status=error)
    so the fallback path can decide whether to retry via metar archive.

    The returned `provider` keeps the upstream name (e.g.
    `weather_com_historical`) even on error so audit trails — and the
    metar fallback's "primary X failed" note — point at the real source
    rather than a generic "none".
    """
    domain = target.source_domain
    provider_for_domain = {
        "wunderground.com": WeatherComHistoricalActualsProvider.provider,
        "weather.gov.hk": HkoDailyExtractActualsProvider.provider,
        "weather.gov": SynopticTimeseriesActualsProvider.provider,
    }.get(domain, "none")
    try:
        if domain == "wunderground.com":
            return WeatherComHistoricalActualsProvider().collect(target, station)
        if domain == "weather.gov.hk":
            return HkoDailyExtractActualsProvider().collect(target, station)
        if domain == "weather.gov":
            return SynopticTimeseriesActualsProvider().collect(target, station)
    except requests.RequestException as exc:
        err = error_actual_for_target(target, str(exc))
        # Preserve the upstream provider so the fallback's audit note
        # can identify which source actually failed.
        return ActualTemperature(
            slug=err.slug,
            station_id=err.station_id,
            target_date=err.target_date,
            target_extreme=err.target_extreme,
            resolution_unit=err.resolution_unit,
            observed_temp_c=err.observed_temp_c,
            observed_temp_f=err.observed_temp_f,
            observed_resolution_value=err.observed_resolution_value,
            provider=provider_for_domain,
            status=err.status,
            sample_count=err.sample_count,
            source_url=err.source_url,
            notes=err.notes,
        )
    return _pending(target, "none", f"unsupported source domain: {domain}")


def _station_local_offset_hours(station: StationMetadata | None) -> float:
    """Approximate the station's timezone offset from longitude.

    Falls back to 0.0 (UTC) when longitude is missing — caller still
    gets a usable window, just centred on UTC midnight, which is the
    same behaviour we had before this fallback existed. This is an
    intentional graceful degradation: a Polymarket market resolved
    "May 12 in San Francisco" with no station coordinates known is
    rare, but if it happens we'd rather sample 24h of UTC than skip
    the position entirely.
    """
    if station is None or station.longitude is None:
        return 0.0
    return float(station.longitude) / 15.0


def _local_day_utc_window(
    target_date: date, offset_hours: float
) -> tuple[datetime, datetime] | None:
    """Return (start_utc_inclusive, end_utc_exclusive) covering the
    full local day at the given UTC offset.

    For Shanghai (offset +8): May 12 local = May 11 16:00 UTC ..
    May 12 16:00 UTC. For Honolulu (offset -10): May 12 local =
    May 12 10:00 UTC .. May 13 10:00 UTC.
    """
    if abs(offset_hours) > 14.0:
        # Earth's real timezones are within ±14h; bigger means our
        # `lon/15` proxy hit a corrupt station record.
        return None
    start_local = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    start_utc = start_local - timedelta(hours=offset_hours)
    end_utc = start_utc + timedelta(days=1)
    return start_utc, end_utc


def _iter_metar_temps_in_window(
    history_root: Path,
    station_id: str,
    start_utc: datetime,
    end_utc_exclusive: datetime,
) -> Iterable[float]:
    """Stream temperatures from `metar_history/<UTC-day>.csv` files
    whose UTC date range overlaps the requested window.

    Up to three files are read (`start_utc.date()`,
    `start_utc.date()+1`, `start_utc.date()+2`) — enough to cover any
    24h window that crosses one or two UTC-midnights.
    """
    seen_at: set[str] = set()
    cursor = start_utc.date()
    end_date = end_utc_exclusive.date()
    while cursor <= end_date:
        path = history_root / f"{cursor.isoformat()}.csv"
        cursor = cursor + timedelta(days=1)
        if not path.exists():
            continue
        try:
            with path.open("r", newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    sid = (row.get("station_id") or "").strip().upper()
                    if sid != station_id:
                        continue
                    observed_at = (row.get("observed_at") or "").strip()
                    if not observed_at or observed_at in seen_at:
                        continue
                    parsed = _parse_iso_utc(observed_at)
                    if parsed is None:
                        continue
                    if parsed < start_utc or parsed >= end_utc_exclusive:
                        continue
                    temp = _as_float(row.get("temp_c"))
                    if temp is None:
                        continue
                    seen_at.add(observed_at)
                    yield temp
        except OSError:
            continue


def _parse_iso_utc(value: str) -> datetime | None:
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def error_actual_for_target(target: MarketTarget, notes: str) -> ActualTemperature:
    return ActualTemperature(
        slug=target.slug,
        station_id=target.station_id,
        target_date=target.target_date,
        target_extreme=target.target_extreme,
        resolution_unit=target.target_unit,
        observed_temp_c=None,
        observed_temp_f=None,
        observed_resolution_value=None,
        provider="none",
        status="error",
        notes=notes,
    )


def _is_finalized_enough(target_date: date, today: date | None, lag_days: int) -> bool:
    reference = today or date.today()
    return target_date <= reference - timedelta(days=lag_days)


def _actual(
    target: MarketTarget,
    provider: str,
    observed_temp_c: float,
    observed_resolution_value: float,
    sample_count: int,
    source_url: str,
) -> ActualTemperature:
    return ActualTemperature(
        slug=target.slug,
        station_id=target.station_id,
        target_date=target.target_date,
        target_extreme=target.target_extreme,
        resolution_unit=target.target_unit,
        observed_temp_c=observed_temp_c,
        observed_temp_f=celsius_to_fahrenheit(observed_temp_c),
        observed_resolution_value=observed_resolution_value,
        provider=provider,
        status="ok",
        sample_count=sample_count,
        source_url=source_url,
    )


def _pending(
    target: MarketTarget,
    provider: str,
    notes: str,
    source_url: str = "",
) -> ActualTemperature:
    return ActualTemperature(
        slug=target.slug,
        station_id=target.station_id,
        target_date=target.target_date,
        target_extreme=target.target_extreme,
        resolution_unit=target.target_unit,
        observed_temp_c=None,
        observed_temp_f=None,
        observed_resolution_value=None,
        provider=provider,
        status="pending",
        source_url=source_url,
        notes=notes,
    )


def _extract_synoptic_temperatures(observations: dict[str, Any]) -> list[float]:
    raw = observations.get("air_temp_set_1") or observations.get("air_temp_value_1")
    if isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, list):
                raw = value
                break
    if not isinstance(raw, list):
        return []
    return [value for value in (_as_float(item) for item in raw) if value is not None]


def _as_float(value: Any) -> float | None:
    if value in {"", None, "M"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _redacted_url(url: str, params: dict[str, Any]) -> str:
    redacted = []
    for key, value in params.items():
        display = "REDACTED" if key.lower() in {"apikey", "token"} else value
        redacted.append(f"{key}={display}")
    return f"{url}?{'&'.join(redacted)}"
