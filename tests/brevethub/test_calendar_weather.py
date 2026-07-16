"""BrevetHub calendar weather badge — cache-read-only rendering.

Follows the established BrevetHub test pattern: monkeypatch `brevethub.models.*`,
use the `client` fixture, never touch a real DB or network. The weather contract
is first-class:
  - a near-term event with a cached forecast renders a compact weather badge
    (condition, temp range, wind, precip) built from the rp_brevet_weather cache,
  - an event with NO cached forecast renders the honest "Forecast not available
    yet" state — never a fabricated value,
  - the calendar is CACHE-READ-ONLY: a normal page load makes ZERO Open-Meteo/RWGPS
    calls (proven by making any live weather fetch on the request path raise, and
    asserting the page still renders the cached badge).
"""
from datetime import datetime, timezone
from unittest.mock import patch

_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes Lighthouse 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200, 'rwgps_url': None,
    'start_location': None, 'start_time': None, 'time_limit_hours': 13.5,
    'signup_count': 0,
}

# A raw Open-Meteo daily payload as the cron would have stored it.
_RAW = {'daily': {'time': ['2026-08-15'], 'weather_code': [61],
                  'temperature_2m_max': [22.4], 'temperature_2m_min': [9.1],
                  'precipitation_sum': [3.2], 'precipitation_probability_max': [65],
                  'wind_speed_10m_max': [18.3], 'wind_direction_10m_dominant': [315]}}

_CACHE_HIT = {11: {'weather_data': _RAW, 'forecast_date': '2026-08-15',
                   'fetched_at': datetime(2026, 8, 10, tzinfo=timezone.utc)}}


def _now():
    return datetime.now(timezone.utc)


def test_calendar_renders_weather_badge_from_cache(client):
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]), \
         patch('brevethub.models.get_brevet_weather_for_events', return_value=_CACHE_HIT):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'event-weather' in body            # badge container renders
    assert 'light rain' in body               # WMO condition text
    assert '48.4' in body and '72.3' in body  # temp range °F (preformatted)
    assert '11.4 mph' in body                 # wind speed mph
    assert 'NW' in body                       # compass label
    assert '65% precip' in body               # precip probability
    assert 'Forecast not available yet' not in body


def test_calendar_shows_not_available_without_cache(client):
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]), \
         patch('brevethub.models.get_brevet_weather_for_events', return_value={}):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Forecast not available yet' in body
    # No fabricated forecast values leak in when there is no cache row.
    assert 'light rain' not in body


def test_calendar_is_cache_read_only_no_live_fetch(client):
    """A normal /calendar load must make ZERO live weather calls. Make every live
    Open-Meteo fetch path on the request explode; the page must still render the
    cached badge (proving it came from the cache, not the network)."""
    boom = AssertionError('calendar made a live weather fetch on the request path')
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]), \
         patch('brevethub.models.get_brevet_weather_for_events', return_value=_CACHE_HIT), \
         patch('shared.weather.requests.get', side_effect=boom), \
         patch('shared.weather.fetch_point_forecast', side_effect=boom) as mfetch:
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'light rain' in body          # badge still rendered — from cache
    mfetch.assert_not_called()


def test_calendar_renders_cleanly_with_mixed_cache(client):
    """A page with one cached and one uncached event renders a badge for the first
    and the 'not available' state for the second — full render, no missing-filter
    500 (BrevetHub does not inherit Team Asha's commafy/clean_name filters)."""
    e2 = dict(_EVENT, id=12, name='Ferry Building 300', distance_km=300,
              date='2026-12-20')
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT, e2]), \
         patch('brevethub.models.get_brevet_weather_for_events', return_value=_CACHE_HIT):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'light rain' in body                    # event 11 has a cached badge
    assert 'Forecast not available yet' in body    # event 12 has none
    assert 'Ferry Building 300' in body
