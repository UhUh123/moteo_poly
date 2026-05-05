from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from detect_temperature.sources.open_meteo import OpenMeteoForecastProvider
from detect_temperature.sources.aviation_weather import AviationWeatherMetarProvider
from detect_temperature.sources.base import StationMetadata


def test_open_meteo_provider_parses_daily_response() -> None:
    """OpenMeteoForecastProvider should correctly parse API response."""
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "daily": {
            "time": ["2025-04-15"],
            "temperature_2m_max": [28.5],
            "temperature_2m_min": [15.2],
            "temperature_2m_mean": [21.8],
        },
        "hourly": {
            "time": [f"2025-04-15T{h:02d}:00" for h in range(24)],
            "temperature_2m": [20.0] * 24,
        },
    }

    provider = OpenMeteoForecastProvider()
    station = StationMetadata(
        station_id="TEST",
        latitude=35.0,
        longitude=139.0,
    )

    with patch("detect_temperature.sources.open_meteo.requests.get", return_value=mock_response):
        forecast = provider.forecast_daily(station, date(2025, 4, 15))

    assert forecast.station_id == "TEST"
    assert forecast.target_date == date(2025, 4, 15)
    assert forecast.provider == "open_meteo"
    assert forecast.temp_max_c == 28.5
    assert forecast.temp_min_c == 15.2
    assert forecast.temp_mean_c == 21.8
    assert forecast.temp_spread_c == pytest.approx(13.3)
    assert len(forecast.hourly_temperature_c) == 24


def test_open_meteo_provider_rejects_missing_coordinates() -> None:
    """Provider should raise when station lacks coordinates."""
    provider = OpenMeteoForecastProvider()
    station = StationMetadata(station_id="NO_COORDS", latitude=None, longitude=None)

    with pytest.raises(ValueError, match="has no coordinates"):
        provider.forecast_daily(station, date(2025, 4, 15))


def test_aviation_weather_metar_parses_response() -> None:
    """AviationWeatherMetarProvider should parse METAR JSON correctly."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "icaoId": "KHOU",
            "obsTime": "2025-04-15T14:00:00Z",
            "temp": 22.0,
            "dewp": 18.0,
            "slp": 1013.2,
            "wspd": 10,
        }
    ]

    provider = AviationWeatherMetarProvider()

    with patch("detect_temperature.sources.aviation_weather.requests.get", return_value=mock_response):
        obs = provider.latest("KHOU")

    assert obs is not None
    assert obs.station_id == "KHOU"
    assert obs.provider == "aviationweather_metar"
    assert obs.temp_c == 22.0
    assert obs.dewpoint_c == 18.0
    assert obs.pressure_hpa == 1013.2
    assert obs.wind_speed_mps == pytest.approx(5.14444, abs=0.001)
    assert obs.observed_at is not None


def test_aviation_weather_metar_returns_none_for_empty() -> None:
    """Provider should return None when no METAR data available (204)."""
    mock_response = MagicMock()
    mock_response.status_code = 204

    provider = AviationWeatherMetarProvider()

    with patch("detect_temperature.sources.aviation_weather.requests.get", return_value=mock_response):
        obs = provider.latest("FAKE")

    assert obs is None
