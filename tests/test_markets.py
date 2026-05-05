from __future__ import annotations

from datetime import date

from detect_temperature.markets import normalize_market


def test_normalizes_wunderground_temperature_market() -> None:
    target = normalize_market(
        {
            "title": "Highest temperature in Houston on May 5?",
            "slug": "highest-temperature-in-houston-on-may-5-2026",
            "location": "William P. Hobby",
            "resolution_source_url": "https://www.wunderground.com/history/daily/us/tx/houston/KHOU",
            "description": "recorded at the William P. Hobby Airport Station in degrees Fahrenheit on 5 May '26.",
        }
    )

    assert target.city == "Houston"
    assert target.target_date == date(2026, 5, 5)
    assert target.target_extreme == "max"
    assert target.target_unit == "fahrenheit"
    assert target.station_id == "KHOU"
    assert target.source_domain == "wunderground.com"


def test_unknown_non_point_market_is_not_for_training() -> None:
    target = normalize_market(
        {
            "title": "May 2026 Temperature Increase (ºC)",
            "slug": "may-2026-temperature-increase-c",
            "location": "",
            "resolution_source_url": "https://data.giss.nasa.gov/",
            "description": "",
        }
    )

    assert target.target_extreme == "unknown"
    assert target.target_date is None
    assert target.station_id == ""


def test_hong_kong_observatory_gets_manual_station_id() -> None:
    target = normalize_market(
        {
            "title": "Lowest temperature in Hong Kong on May 6?",
            "slug": "lowest-temperature-in-hong-kong-on-may-6-2026",
            "location": "Hong Kong on May 6",
            "resolution_source_url": "https://www.weather.gov.hk/en/cis/climat.htm",
            "description": "lowest temperature recorded by the Hong Kong Observatory in degrees Celsius on 6 May '26.",
        }
    )

    assert target.location_name == "Hong Kong Observatory"
    assert target.target_date == date(2026, 5, 6)
    assert target.target_extreme == "min"
    assert target.station_id == "HKO"


def test_weather_gov_timeseries_uses_site_query_value() -> None:
    target = normalize_market(
        {
            "title": "Highest temperature in Tel Aviv on May 5?",
            "slug": "highest-temperature-in-tel-aviv-on-may-5-2026",
            "location": "Ben Gurion International Airport",
            "resolution_source_url": "https://www.weather.gov/wrh/timeseries?site=LLBG",
            "description": "highest temperature recorded by NOAA at the Ben Gurion International Airport in degrees Celsius on 5 May '26.",
        }
    )

    assert target.station_id == "LLBG"


def test_celsius_market_unit_ignores_toggle_hint() -> None:
    target = normalize_market(
        {
            "title": "Lowest temperature in Shanghai on May 6?",
            "slug": "lowest-temperature-in-shanghai-on-may-6-2026",
            "location": "Shanghai Pudong International",
            "resolution_source_url": "https://www.wunderground.com/history/daily/cn/shanghai/ZSPD",
            "description": (
                "This market will resolve to the temperature range that contains the lowest temperature "
                "recorded at the Shanghai Pudong International Airport Station in degrees Celsius on 6 May '26.\n"
                "To toggle between Fahrenheit and Celsius, switch the Temperature setting between °F and °C.\n"
                "The resolution source for this market measures temperatures to whole degrees Celsius (eg, 9°C)."
            ),
        }
    )

    assert target.target_unit == "celsius"
