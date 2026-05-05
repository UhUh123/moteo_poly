from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

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


def collect_actual_for_target(
    target: MarketTarget,
    station: StationMetadata | None,
    today: date | None = None,
    finalization_lag_days: int = 1,
) -> ActualTemperature:
    if target.target_date is None:
        return _pending(target, "none", "missing target date")
    if not _is_finalized_enough(target.target_date, today=today, lag_days=finalization_lag_days):
        return _pending(target, "none", f"waiting for finalization lag of {finalization_lag_days} day(s)")

    domain = target.source_domain
    if domain == "wunderground.com":
        return WeatherComHistoricalActualsProvider().collect(target, station)
    if domain == "weather.gov.hk":
        return HkoDailyExtractActualsProvider().collect(target, station)
    if domain == "weather.gov":
        return SynopticTimeseriesActualsProvider().collect(target, station)
    return _pending(target, "none", f"unsupported source domain: {domain}")


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
