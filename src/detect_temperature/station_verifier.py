from __future__ import annotations

import re
from typing import Any

from .schema import MarketTarget
from .sources.base import StationMetadata


SUPPORTED_DOMAINS = {"wunderground.com", "weather.gov", "weather.gov.hk"}


def verify_target(
    target: MarketTarget,
    station: StationMetadata | None = None,
) -> tuple[bool, str]:
    """Decide whether a target's resolution source can be trusted.

    Returns (verified, reason). A target is only verified when we can tie its
    resolution source to a concrete station with coordinates. The goal is to
    refuse trading any market whose station, source domain, or unit we can't
    pin down — station mismatch is the single biggest sink of paper edge, and
    the risk investigation notes flag it as the highest-priority guard.
    """
    if not target.station_id:
        return False, "missing station_id"

    domain = (target.source_domain or "").lower()
    if domain and domain not in SUPPORTED_DOMAINS:
        return False, f"unsupported source domain: {domain}"

    target_unit = (target.target_unit or "").lower()
    if target_unit not in {"celsius", "fahrenheit"}:
        return False, f"unknown resolution unit: {target.target_unit!r}"

    if target.target_extreme not in {"max", "min"}:
        return False, f"unknown target_extreme: {target.target_extreme!r}"

    if target.target_date is None:
        return False, "missing target_date"

    if domain == "wunderground.com":
        if not re.fullmatch(r"[A-Z0-9]{3,5}", target.station_id):
            return False, f"wunderground station id not ICAO-like: {target.station_id}"

    if domain == "weather.gov.hk":
        if target.station_id != "HKO":
            return False, "weather.gov.hk source requires HKO station id"
        if "Hong Kong Observatory" not in (target.description or ""):
            return False, "HKO description does not mention Hong Kong Observatory"

    if domain == "weather.gov":
        if not re.fullmatch(r"[A-Z0-9]{3,5}", target.station_id):
            return False, f"weather.gov station id not recognized: {target.station_id}"

    if station is not None:
        if station.latitude is None or station.longitude is None:
            return False, f"station {target.station_id} has no coordinates"
        if station.station_id.upper() != target.station_id.upper():
            return False, (
                f"station catalog id {station.station_id!r} disagrees with "
                f"target station id {target.station_id!r}"
            )

    return True, "station verified"


def annotate_feature_row(
    row: dict[str, Any],
    target: MarketTarget,
    station: StationMetadata | None,
) -> dict[str, Any]:
    verified, reason = verify_target(target, station)
    row["station_verified"] = int(verified)
    row["station_verification_reason"] = reason
    return row
