"""Pinned regression: extracting the per-stop wind loop into the shared
``compute_stop_winds`` must NOT change ``services.weather.fetch_stop_wind``'s output,
and must leave ``get_historical_stop_wind`` untouched.

``fetch_stop_wind`` used to compute the per-stop dict inline; it now reads the stored
forecast and delegates the math to ``shared.weather.compute_stop_winds``, then projects
the result back to its exact legacy key set. These tests pin representative inputs and
assert the EXACT output dict, so any behavioral drift (a changed value, a leaked/dropped
key) fails the build. All weather is mocked — no DB, no network.
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch


# Constant hourly arrays keyed to TODAY so get_hour_index resolves; constant values
# make the exact arrival hour irrelevant (every hour reads the same wind).
def _const_forecast(day, wind_speed=20.0, wind_dir=270, temp=15.0):
    times = [f"{day:%Y-%m-%d}T{h:02d}:00" for h in range(24)]
    return {'hourly': {
        'time': times,
        'wind_speed_10m': [wind_speed] * 24,
        'wind_direction_10m': [wind_dir] * 24,
        'wind_gusts_10m': [wind_speed + 5] * 24,
        'temperature_2m': [temp] * 24,
    }}


# A due-south route (constant lng, decreasing lat → bearing 180) with a 270° (west)
# wind is a pure crosswind — a stable, hand-verifiable fixture.
_SAMPLES = [
    {'lat': 37.80, 'lng': -122.40, 'distance_m': 0},
    {'lat': 37.70, 'lng': -122.40, 'distance_m': 16093},
]
_STOPS = [{'distance_miles': 0.0, 'arrival_time_min': 0}]


# --------------------------------------------------------------------------- #
# fetch_stop_wind — exact legacy output preserved after the extraction.
# --------------------------------------------------------------------------- #
def test_fetch_stop_wind_output_pinned():
    from services.weather import fetch_stop_wind
    today = date.today()
    weather = [_const_forecast(today), _const_forecast(today)]
    with patch('services.weather.load_stored_route_weather',
               return_value=(weather, _SAMPLES)), \
         patch('services.weather.fetch_route_weather',
               side_effect=AssertionError('live fetch on the read path!')):
        result = fetch_stop_wind(_STOPS, 555, today, '06:00')

    assert result == [{
        'wind_speed_kmh': 20.0,
        'wind_speed_mph': 12.4,
        'headwind_kmh': 0.0,
        'crosswind_kmh': 20.0,
        'wind_type': 'crosswind',
        'wind_arrow_deg': 270,
        'wind_direction_deg': 270,
        'rider_bearing_deg': 180,
        'style': {'color': '#2563EB', 'background': 'rgba(37,99,235,0.65)',
                  'font_size': '1.0rem'},
        'label': 'crosswind / light',
        'temperature_c': 15.0,
        'temperature_f': 59,
    }]


def test_fetch_stop_wind_keys_are_exactly_legacy():
    """The extraction must not leak the new shared keys (arrow_rotation/arrow_glyph/
    compass) into fetch_stop_wind's output — the ride-plan table reads the legacy set."""
    from services.weather import fetch_stop_wind
    today = date.today()
    weather = [_const_forecast(today), _const_forecast(today)]
    with patch('services.weather.load_stored_route_weather',
               return_value=(weather, _SAMPLES)):
        result = fetch_stop_wind(_STOPS, 555, today, '06:00')
    assert set(result[0].keys()) == {
        'wind_speed_kmh', 'wind_speed_mph', 'headwind_kmh', 'crosswind_kmh',
        'wind_type', 'wind_arrow_deg', 'wind_direction_deg', 'rider_bearing_deg',
        'style', 'label', 'temperature_c', 'temperature_f',
    }


def test_fetch_stop_wind_matches_shared_compute():
    """fetch_stop_wind is exactly compute_stop_winds projected to the legacy keys."""
    from services.weather import fetch_stop_wind
    from shared.weather import compute_stop_winds
    today = date.today()
    weather = [_const_forecast(today), _const_forecast(today)]
    legacy_keys = ('wind_speed_kmh', 'wind_speed_mph', 'headwind_kmh', 'crosswind_kmh',
                   'wind_type', 'wind_arrow_deg', 'wind_direction_deg',
                   'rider_bearing_deg', 'style', 'label', 'temperature_c',
                   'temperature_f')
    with patch('services.weather.load_stored_route_weather',
               return_value=(weather, _SAMPLES)):
        result = fetch_stop_wind(_STOPS, 555, today, '06:00')
    shared = compute_stop_winds(_STOPS, weather, _SAMPLES, today, '06:00')
    assert result == [{k: shared[0][k] for k in legacy_keys}]


# --------------------------------------------------------------------------- #
# get_historical_stop_wind — untouched by the extraction (pinned subset).
# --------------------------------------------------------------------------- #
def test_get_historical_stop_wind_unchanged():
    from services.weather import get_historical_stop_wind
    ride_date = date.today() - timedelta(days=10)
    track = [
        {'y': 37.80, 'x': -122.40, 'd': 0, 'e': 10},
        {'y': 37.70, 'x': -122.40, 'd': 16093, 'e': 20},
        {'y': 37.60, 'x': -122.40, 'd': 32186, 'e': 30},
    ]
    stops = [
        {'distance_miles': 0.0, 'arrival_time_min': 0, 'stop_name': 'Start'},
        {'distance_miles': 10.0, 'arrival_time_min': 60, 'stop_name': 'C1'},
    ]
    weather = [_const_forecast(ride_date), _const_forecast(ride_date)]
    with patch('services.weather.fetch_historical_wind',
               return_value=(weather, 'archive')):
        rows, source = get_historical_stop_wind(stops, track, ride_date, ride_id=None)

    assert source == 'archive'
    assert len(rows) == 2
    assert rows[0]['stop_order'] == 0
    assert rows[0]['stop_name'] == 'Start'
    assert rows[0]['wind_speed_kmh'] == 20.0
    assert rows[0]['wind_type'] == 'crosswind'
    assert rows[0]['wind_arrow_deg'] == 270
    assert rows[0]['headwind_kmh'] == 0.0
    assert rows[0]['crosswind_kmh'] == 20.0
    assert rows[0]['data_source'] == 'archive'
