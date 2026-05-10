"""Near-close re-pricing helpers.

As a daily Polymarket weather market approaches close, two things change:

  1. We start accumulating observed max / min for the day. If the observed
     max has already exceeded a bucket's upper bound, the market is
     effectively decided and every bucket below is impossible.
  2. The remaining-hours forecast uncertainty shrinks. Our sigma from
     calibration was calibrated for a 24h-ahead forecast; with only T
     hours left it should scale roughly with sqrt(T / 24).

This module encodes both effects so `refresh-open-positions` can decide
whether a paper position still has edge worth holding.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests


OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "detect-temperature/0.1 near-close"
SQRT_TWO = math.sqrt(2.0)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT_TWO))


def _interval_probability(
    mean: float,
    sigma: float,
    lower: float | None,
    upper: float | None,
) -> float:
    if sigma <= 0:
        # degenerate: deterministic forecast at `mean`
        if (lower is None or mean >= lower) and (upper is None or mean < upper):
            return 1.0
        return 0.0
    lo = 0.0 if lower is None else _normal_cdf((lower - mean) / sigma)
    hi = 1.0 if upper is None else _normal_cdf((upper - mean) / sigma)
    return max(0.0, min(1.0, hi - lo))


@dataclass(frozen=True)
class NearCloseInput:
    """Everything `refined_bucket_probability` needs.

    `mean_c`, `sigma_c` are the calibrated forecast for the day max/min.
    `observed_so_far_c` is the partial-day max (for target_extreme="max")
    or min (for "min"); None if we have no fresh observation yet.
    `hours_remaining` is how many hours of the event day still need to
    happen before the market resolves. 24.0 == no shrink.
    """
    target_extreme: str
    mean_c: float
    sigma_c: float
    lower_c: float | None
    upper_c: float | None
    observed_so_far_c: float | None = None
    hours_remaining: float = 24.0


def shrink_sigma(sigma_c: float, hours_remaining: float) -> float:
    """sigma_eff scales with sqrt(T/24) with a 0.25 floor so we never
    claim arbitrarily high certainty from a noisy intraday observation."""
    if hours_remaining <= 0:
        return 0.0
    fraction = max(0.0, min(1.0, hours_remaining / 24.0))
    scale = math.sqrt(fraction)
    return max(0.25 * sigma_c, sigma_c * scale) if sigma_c > 0 else 0.0


def refined_bucket_probability(spec: NearCloseInput) -> float:
    """P(day's extreme lands in [lower, upper]) given observed-so-far.

    Returns the fair probability of being inside the bucket:
      - target_extreme="max": final_max = max(observed_so_far, future_peak)
      - target_extreme="min": final_min = min(observed_so_far, future_low)

    The future component is Normal(mean, shrunk_sigma). We treat the
    observed_so_far as certain (we already saw it).
    """
    lower = spec.lower_c
    upper = spec.upper_c
    mean = spec.mean_c
    sigma_eff = shrink_sigma(spec.sigma_c, spec.hours_remaining)
    obs = spec.observed_so_far_c

    if obs is None:
        return _interval_probability(mean, sigma_eff, lower, upper)

    if spec.target_extreme == "max":
        # If observed max already exceeded upper bound, bucket impossible
        if upper is not None and obs >= upper:
            return 0.0
        # If observed max is above lower bound, bucket wins iff future peak
        # stays below upper (daily_max = max(obs, future) is already >= lower)
        if lower is not None and obs >= lower:
            if upper is None:
                return 1.0
            # P(future <= upper) — probability the rest of the day stays below
            return _interval_probability(mean, sigma_eff, None, upper)
        # obs is below lower: need future peak in [lower, upper]
        return _interval_probability(mean, sigma_eff, lower, upper)

    if spec.target_extreme == "min":
        # Symmetric for min: if observed min already below lower, bucket impossible
        if lower is not None and obs < lower:
            return 0.0
        if upper is not None and obs < upper:
            if lower is None:
                return 1.0
            return _interval_probability(mean, sigma_eff, lower, None)
        return _interval_probability(mean, sigma_eff, lower, upper)

    # Unknown extreme — behave like the plain forecast
    return _interval_probability(mean, sigma_eff, lower, upper)


def hours_remaining_until(close_utc: datetime, now_utc: datetime | None = None) -> float:
    """Helper: hours between now and close, clamped to [0, 48]."""
    ref = now_utc or datetime.now(timezone.utc)
    if close_utc.tzinfo is None:
        close_utc = close_utc.replace(tzinfo=timezone.utc)
    seconds = (close_utc - ref).total_seconds()
    return max(0.0, min(48.0, seconds / 3600.0))


@dataclass(frozen=True)
class IntradayObservation:
    """Partial-day observed max/min for a station, and how stale each is."""
    station_id: str
    target_date: date
    observed_max_c: float | None
    observed_min_c: float | None
    samples: int
    fetched_at: datetime


def fetch_intraday_max_min(
    latitude: float,
    longitude: float,
    target_date: date,
    station_id: str = "",
    now_utc: datetime | None = None,
    timeout_s: int = 20,
    endpoint: str = OPEN_METEO_FORECAST,
) -> IntradayObservation:
    """Fetch hourly temperatures for `target_date` from Open-Meteo and
    return running max/min over all hours that already happened (strictly
    before `now_utc`). Returns None, None if no hours have passed.

    Open-Meteo's `/v1/forecast` returns both past and future hours of the
    current day, so one call is enough. `timezone=UTC` keeps the hour
    indexing aligned with `now_utc`.
    """
    now = now_utc or datetime.now(timezone.utc)
    response = requests.get(
        endpoint,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "hourly": "temperature_2m",
            "timezone": "UTC",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=timeout_s,
    )
    response.raise_for_status()
    payload = response.json().get("hourly") or {}
    times = payload.get("time") or []
    temps = payload.get("temperature_2m") or []
    observed: list[float] = []
    for iso_time, temp in zip(times, temps, strict=False):
        if temp is None:
            continue
        try:
            stamp = datetime.fromisoformat(iso_time)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp > now:
            continue
        try:
            observed.append(float(temp))
        except (TypeError, ValueError):
            continue
    return IntradayObservation(
        station_id=station_id.upper(),
        target_date=target_date,
        observed_max_c=max(observed) if observed else None,
        observed_min_c=min(observed) if observed else None,
        samples=len(observed),
        fetched_at=now,
    )
