from __future__ import annotations

from datetime import date

from detect_temperature.markets import normalize_market
from detect_temperature.sources.actuals import (
    _extract_synoptic_temperatures,
    _is_finalized_enough,
    error_actual_for_target,
)


def test_extract_synoptic_temperatures_from_primary_key() -> None:
    assert _extract_synoptic_temperatures({"air_temp_set_1": [12.1, None, "13.4", "M"]}) == [12.1, 13.4]


def test_finalization_lag_skips_today() -> None:
    assert not _is_finalized_enough(date(2026, 5, 4), today=date(2026, 5, 4), lag_days=1)
    assert _is_finalized_enough(date(2026, 5, 3), today=date(2026, 5, 4), lag_days=1)


def test_error_actual_preserves_target_identity() -> None:
    target = normalize_market(
        {
            "title": "Highest temperature in Houston on May 5?",
            "slug": "highest-temperature-in-houston-on-may-5-2026",
            "location": "William P. Hobby",
            "resolution_source_url": "https://www.wunderground.com/history/daily/us/tx/houston/KHOU",
            "description": "recorded at the William P. Hobby Airport Station in degrees Fahrenheit on 5 May '26.",
        }
    )

    actual = error_actual_for_target(target, "boom")

    assert actual.slug == target.slug
    assert actual.status == "error"
    assert actual.notes == "boom"

