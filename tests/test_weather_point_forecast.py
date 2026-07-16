"""Scope-A point-forecast helpers in the shared weather engine.

These cover the BrevetHub-facing additions to ``shared/weather.py`` — region ->
coordinate resolution, compass/wind-arrow helpers, the keyless single-point
Open-Meteo fetch (mocked; NEVER a real network call), and the pure summarizer.
The extraction is also exercised through the ``services.weather`` compatibility
shim to prove the re-export keeps the public path working.
"""
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from shared import weather as sw


# --------------------------------------------------------------------------- #
# The extraction re-exports through services.weather (shim compatibility)
# --------------------------------------------------------------------------- #
def test_shim_reexports_shared_symbols():
    import services.weather as svc
    # Point-forecast additions are reachable through the old import path.
    assert svc.resolve_region_coordinates is sw.resolve_region_coordinates
    assert svc.summarize_point_forecast is sw.summarize_point_forecast
    # A core math primitive is the same object (single source of truth).
    assert svc.calculate_bearing is sw.calculate_bearing
    # The model-coupled functions stay in the shim, NOT in shared.
    assert hasattr(svc, 'load_stored_route_weather')
    assert not hasattr(sw, 'load_stored_route_weather')


# --------------------------------------------------------------------------- #
# Region -> coordinate resolution
# --------------------------------------------------------------------------- #
def test_resolve_region_known_state():
    coords = sw.resolve_region_coordinates('CA: San Francisco')
    assert coords is not None
    lat, lng = coords
    assert 32 < lat < 42 and -125 < lng < -114   # somewhere in California


def test_resolve_region_blank_or_none():
    assert sw.resolve_region_coordinates(None) is None
    assert sw.resolve_region_coordinates('') is None


def test_resolve_region_unknown_or_foreign():
    # A non-US / unknown state prefix has no centroid -> None (never fabricated).
    assert sw.resolve_region_coordinates('ON: Toronto') is None
    assert sw.resolve_region_coordinates('ZZ: Nowhere') is None


def test_resolve_region_is_case_insensitive_on_prefix():
    assert sw.resolve_region_coordinates('tx: Austin') == sw._US_STATE_CENTROIDS['TX']


# --------------------------------------------------------------------------- #
# Compass + wind-arrow helpers
# --------------------------------------------------------------------------- #
def test_compass_label_cardinals():
    assert sw.compass_label(0) == 'N'
    assert sw.compass_label(90) == 'E'
    assert sw.compass_label(180) == 'S'
    assert sw.compass_label(270) == 'W'
    assert sw.compass_label(315) == 'NW'


def test_wind_travel_rotation_points_where_wind_goes():
    # Meteorological FROM -> arrow points to the TRAVEL direction (opposite).
    assert sw.wind_travel_rotation(0) == 180     # north wind travels south
    assert sw.wind_travel_rotation(90) == 270    # east wind travels west
    assert sw.wind_travel_rotation(180) == 0     # south wind travels north


# --------------------------------------------------------------------------- #
# Single-point Open-Meteo fetch (mocked — no real network)
# --------------------------------------------------------------------------- #
def _daily_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {'daily': {
        'time': ['2026-08-15'],
        'weather_code': [61], 'temperature_2m_max': [22.4],
        'temperature_2m_min': [9.1], 'precipitation_sum': [3.2],
        'precipitation_probability_max': [65], 'wind_speed_10m_max': [18.3],
        'wind_direction_10m_dominant': [315],
    }}
    return resp


def test_fetch_point_forecast_in_horizon_calls_openmeteo():
    target = date.today() + timedelta(days=3)
    with patch('shared.weather.requests.get', return_value=_daily_response()) as mget:
        data = sw.fetch_point_forecast(37.2, -119.4, target)
    assert mget.called
    # Hits the keyless forecast endpoint with the pinned date and daily params.
    args, kwargs = mget.call_args
    assert args[0] == sw.OPEN_METEO_URL
    params = kwargs['params']
    assert params['start_date'] == params['end_date'] == target.strftime('%Y-%m-%d')
    assert 'wind_direction_10m_dominant' in params['daily']
    assert 'timezone' in params
    assert data['daily']['weather_code'] == [61]


def test_fetch_point_forecast_beyond_horizon_returns_none_without_fetch():
    far = date.today() + timedelta(days=30)   # past the 16-day horizon
    with patch('shared.weather.requests.get') as mget:
        assert sw.fetch_point_forecast(37.2, -119.4, far) is None
    mget.assert_not_called()


def test_fetch_point_forecast_past_date_returns_none_without_fetch():
    past = date.today() - timedelta(days=1)
    with patch('shared.weather.requests.get') as mget:
        assert sw.fetch_point_forecast(37.2, -119.4, past) is None
    mget.assert_not_called()


def test_fetch_point_forecast_accepts_iso_string_date():
    target = (date.today() + timedelta(days=2)).strftime('%Y-%m-%d')
    with patch('shared.weather.requests.get', return_value=_daily_response()) as mget:
        data = sw.fetch_point_forecast(37.2, -119.4, target)
    assert mget.called and data is not None


# --------------------------------------------------------------------------- #
# Pure summarizer
# --------------------------------------------------------------------------- #
def test_summarize_point_forecast_full():
    raw = _daily_response().json.return_value
    s = sw.summarize_point_forecast(raw)
    assert s['weather_code'] == 61
    assert s['condition'] == 'light rain'
    assert s['temp_min_c'] == 9.1 and s['temp_max_c'] == 22.4
    assert s['temp_min_f'] == pytest.approx(48.4, abs=0.1)
    assert s['temp_max_f'] == pytest.approx(72.3, abs=0.1)
    assert s['precip_mm'] == 3.2 and s['precip_prob'] == 65
    assert s['wind_speed_kmh'] == 18.3
    assert s['wind_speed_mph'] == pytest.approx(11.4, abs=0.1)
    assert s['wind_dir_deg'] == 315 and s['wind_dir_label'] == 'NW'
    assert s['wind_travel_deg'] == sw.wind_travel_rotation(315)


def test_summarize_point_forecast_empty_or_missing_returns_none():
    assert sw.summarize_point_forecast(None) is None
    assert sw.summarize_point_forecast({}) is None
    assert sw.summarize_point_forecast({'daily': {}}) is None      # no weather_code
    assert sw.summarize_point_forecast('not-a-dict') is None


def test_summarize_point_forecast_tolerates_partial_daily():
    # Only a weather code present — missing metrics come back as None, not a crash.
    s = sw.summarize_point_forecast({'daily': {'weather_code': [3]}})
    assert s is not None
    assert s['condition'] == 'overcast'
    assert s['temp_min_c'] is None and s['wind_speed_mph'] is None
