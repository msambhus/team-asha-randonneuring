"""Multi-leg weather payload composition."""

from unittest.mock import patch
from datetime import datetime

from routes.weather import build_multiday_weather_payload
from routes.riders import _fetch_plan_stop_wind


def _payload(name, distance, day_temp):
    segment = {
        'distance_mi': distance,
        'arrival_time': '6:00 AM',
        'temperature_f': day_temp,
        'feels_like_f': day_temp,
        'wind_speed_mph': 5,
        'wind_gust_mph': 8,
        'headwind_mph': 2,
        'precip_percent': 0,
        'precipitation_mm': 0,
        'cloud_cover': 10,
        'elevation_ft': 100,
        'humidity': 50,
    }
    return {
        'route_name': name,
        'total_distance_mi': distance,
        'total_elevation_ft': 1000,
        'polyline': [[-93, 45]],
        'map_segments': [segment],
        'table_segments': [segment],
        'cue_points': [{'name': 'Control', 'distance_mi': distance}],
    }


def test_multiday_payload_offsets_distances_and_preserves_days():
    legs = [
        {'rwgps_url': 'https://ridewithgps.com/routes/111', 'day_number': 1, 'label': 'Day 1'},
        {'rwgps_url': 'https://ridewithgps.com/routes/222', 'day_number': 2, 'label': 'Day 2'},
    ]
    with patch('routes.weather.build_weather_payload', side_effect=[
             (_payload('One', 100, 60), None),
             (_payload('Two', 120, 70), None)]), \
         patch('routes.weather.generate_ride_summary', return_value='summary'):
        payload, err = build_multiday_weather_payload(
            legs, datetime(2026, 8, 6, 6), plan_name='Grand Ride')
    assert err is None
    assert payload['route_name'] == 'Grand Ride'
    assert payload['total_distance_mi'] == 220
    assert [s['distance_mi'] for s in payload['map_segments']] == [100.0, 220.0]
    assert [s['day_number'] for s in payload['map_segments']] == [1, 2]
    assert payload['chart_data']['labels'] == [100.0, 220.0]
    assert len(payload['legs']) == 2
    assert payload['legs'][0]['polyline'] == [[-93, 45]]
    assert payload['legs'][1]['polyline'] == [[-93, 45]]


def test_multiday_stop_weather_rebases_each_day():
    plan = {'id': 9}
    stops = [
        {'location': 'Day 1: Start', 'distance_miles': 0, 'arrival_time_min': 0},
        {'location': 'Day 1: Finish', 'distance_miles': 100, 'arrival_time_min': 600},
        {'location': 'Day 2: Start', 'distance_miles': 100, 'arrival_time_min': 1440},
        {'location': 'Day 2: Finish', 'distance_miles': 220, 'arrival_time_min': 2100},
    ]
    legs = [
        {'rwgps_url': 'https://ridewithgps.com/routes/111', 'day_number': 1},
        {'rwgps_url': 'https://ridewithgps.com/routes/222', 'day_number': 2},
    ]
    calls = []

    def fake_fetch(local_stops, route_id, forecast_date, start_time):
        calls.append((route_id, forecast_date, local_stops))
        return [{'route': route_id} for _ in local_stops]

    with patch('models.get_ride_plan_legs', return_value=legs), \
         patch('routes.riders.fetch_stop_wind', side_effect=fake_fetch):
        winds = _fetch_plan_stop_wind(
            plan, stops, datetime(2026, 8, 6).date(), '06:00')
    assert [w['route'] for w in winds] == ['111', '111', '222', '222']
    assert calls[1][1].isoformat() == '2026-08-07'
    assert [s['distance_miles'] for s in calls[1][2]] == [0, 120.0]
    assert [s['arrival_time_min'] for s in calls[1][2]] == [0, 660.0]
