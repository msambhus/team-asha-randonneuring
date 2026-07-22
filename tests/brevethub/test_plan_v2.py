"""Render tests for the rpv2 3-tab plan view (Plan / Strategies / Weather).

Follows the BrevetHub test pattern: monkeypatch `brevethub.models.*`, use the `client`
fixture, never touch a real DB or network. A stored real plan now renders the rich
3-tab layout driven by the promoted shared functions. First-class contracts proven
here as full render-path assertions (so a missing filter or bad macro would surface as
a 500, not a silent pass):

  * the Plan tab renders the 11-column itinerary, the journey SVG, the info trio, a
    risks callout, and the snapshot/share card with a de-branded `product_name · /plan/<id>`
    footer (never "Team Asha"),
  * the Strategies tab renders three read-only Comfort/Standard/Push cards with NO save
    button and NO community list,
  * the Weather tab renders the lean per-stop forecast list from the cached route
    weather via compute_stop_winds (the fallback when no Mapbox token is set), NO Mapbox,
  * a guest sees rider local-parts only — no full email, no google_id,
  * all three tabs return 200 (no missing-filter 500).
"""
from unittest.mock import patch

import pytest


_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Cascade Lakes 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'OR: Bend',
    'ride_type': 'ACP brevet', 'elevation_ft': 3280, 'rwgps_url': None,
    'start_location': None, 'start_time': '06:00', 'time_limit_hours': 13.5,
}

# A stored real plan (native miles / mph / feet) with a control, a meal break, and a
# finish — the same shape rp_brevet_route_plan[_stop] returns.
_PLAN = {
    'id': 5, 'event_id': 11, 'variant': 'conservative', 'name': 'Cascade Lakes 200',
    'slug': 'cascade-lakes-200', 'total_distance_miles': 124.3, 'total_elevation_ft': 3280,
    'rwgps_url': 'https://ridewithgps.com/routes/1', 'rwgps_route_id': '1',
    'distance_km': 200, 'cutoff_hours': 13.5, 'start_time': '06:00',
    'avg_moving_speed': 12.0, 'avg_elapsed_speed': 11.5,
    'total_moving_time_min': 534, 'total_elapsed_time_min': 564,
    'total_break_time_min': 30, 'overall_ft_per_mile': 26,
}
_STOPS = [
    {'stop_order': 1, 'location': 'Downtown Start', 'stop_type': 'start',
     'distance_miles': 0.0, 'seg_dist': 0.0, 'elevation_gain': 0, 'ft_per_mi': None,
     'avg_speed': None, 'segment_time_min': 0, 'cum_time_min': 0, 'time_bank_min': None,
     'difficulty_score': 0.0, 'notes': None},
    {'stop_order': 2, 'location': 'Midway Control', 'stop_type': 'control',
     'distance_miles': 62.1, 'seg_dist': 62.1, 'elevation_gain': 1600, 'ft_per_mi': 26,
     'avg_speed': 12.0, 'segment_time_min': 266, 'cum_time_min': 266, 'time_bank_min': 120,
     'difficulty_score': 2.6, 'notes': None},
    {'stop_order': 3, 'location': 'Lunch Stop', 'stop_type': 'meal',
     'distance_miles': 62.1, 'seg_dist': 0.0, 'elevation_gain': 0, 'ft_per_mi': None,
     'avg_speed': None, 'segment_time_min': 30, 'cum_time_min': 296, 'time_bank_min': None,
     'difficulty_score': 0.0, 'notes': 'Lunch — sit-down refuel'},
    {'stop_order': 4, 'location': 'Downtown Finish', 'stop_type': 'finish',
     'distance_miles': 124.3, 'seg_dist': 62.2, 'elevation_gain': 1680, 'ft_per_mi': 27,
     'avg_speed': 11.9, 'segment_time_min': 268, 'cum_time_min': 564, 'time_bank_min': 150,
     'difficulty_score': 2.7, 'notes': None},
]
_BUNDLE = {'plan': _PLAN, 'stops': _STOPS}

# A PII-free roster (email local-part only) as get_event_going_riders returns.
_ROSTER = [{'name': 'alice', 'status': 'going'},
           {'name': 'bob', 'status': 'interested'}]


def _cached_weather():
    """A minimal cached rp_brevet_route_weather row that compute_stop_winds can resolve
    to per-stop wind (same keys the cron warms): a per-sample Open-Meteo hourly forecast
    plus the aligned sample points."""
    times = [f"2026-08-15T{h:02d}:00" for h in range(24)]

    def sample(ws, wd, tc):
        return {'hourly': {'time': times, 'wind_speed_10m': [ws] * 24,
                           'wind_direction_10m': [wd] * 24, 'temperature_2m': [tc] * 24}}

    return {
        'event_id': 11, 'forecast_date': '2026-08-15',
        'weather_data': [sample(20, 270, 28), sample(25, 90, 30), sample(10, 180, 24)],
        'sample_points': [{'lat': 44.0, 'lng': -121.0, 'distance_m': 0},
                          {'lat': 44.1, 'lng': -121.2, 'distance_m': 100000},
                          {'lat': 44.2, 'lng': -121.4, 'distance_m': 200000}],
    }


def _get(client, url, *, weather=None):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=_BUNDLE), \
         patch('brevethub.models.get_brevet_route_weather', return_value=weather), \
         patch('brevethub.models.get_event_going_riders', return_value=_ROSTER):
        return client.get(url)


# --------------------------------------------------------------------------- #
# Plan tab
# --------------------------------------------------------------------------- #
def test_plan_tab_renders_itinerary_and_snapshot(client):
    resp = _get(client, '/plan/11', weather=_cached_weather())
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 3-tab shell.
    assert 'data-tab="plan"' in body and 'data-tab="strategies"' in body and 'data-tab="weather"' in body
    # 11-column itinerary headers.
    for head in ('>Stop<', '>Seg<', '>Cumul<', '>Climb<', '>Pace<', '>Elapsed<',
                 '>ETA<', '>Bank<', '>Wind<'):
        assert head in body, head
    assert 'rpv2-itinerary' in body
    # Real control name + journey chart + info trio.
    assert 'Midway Control' in body
    assert 'rpv2-journey-svg' in body
    assert 'Fuel + breaks' in body and 'Riders' in body
    # Snapshot/share card, de-branded footer.
    assert 'rpv2-snap' in body
    assert 'BrevetHub · /plan/11' in body
    assert 'team asha' not in body.lower()


def test_plan_tab_wind_and_toughness_from_forecast(client):
    resp = _get(client, '/plan/11', weather=_cached_weather())
    body = resp.get_data(as_text=True)
    # A per-stop wind arrow renders (forecast resolved through compute_stop_winds).
    assert 'rpv2-wind-arrow' in body
    # A toughness score chip renders on a moving segment.
    assert 'rpv2-tough' in body


def test_plan_tab_survives_no_forecast(client):
    """A wind-cache miss must still render the itinerary (wind column blank), never 500."""
    resp = _get(client, '/plan/11', weather=None)
    assert resp.status_code == 200
    assert 'Midway Control' in resp.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Strategies tab
# --------------------------------------------------------------------------- #
def test_strategies_tab_read_only_cards(client):
    resp = _get(client, '/plan/11?tab=strategies', weather=None)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="rpv2-panel-strategies"' in body
    # Three pace cards.
    assert 'rpv2-pc-grid' in body
    for name in ('>Comfort<', '>Standard<', '>Push<'):
        assert name in body, name
    # Read-only: no save button, no community list.
    assert 'data-pace-pick' not in body
    assert 'Choose this plan' not in body
    assert 'Community plans' not in body


def test_strategies_cards_carry_wind_when_forecast_cached(client):
    """The strategy cards must surface the SAME per-stop wind/toughness the Plan tab
    does — i.e. the route must pass seg_meta into compute_pace_strategies. Without it
    the cards silently blank (wind_known=False), a parity regression: guard it."""
    resp = _get(client, '/plan/11?tab=strategies', weather=_cached_weather())
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # A per-stop wind arrow inside a strategy card (only rendered when seg_meta carries
    # the resolved forecast wind through to the cards).
    assert 'rpv2-pc-wind-arrow' in body


# --------------------------------------------------------------------------- #
# Weather tab
# --------------------------------------------------------------------------- #
def test_weather_tab_per_stop_forecast(client):
    resp = _get(client, '/plan/11?tab=weather', weather=_cached_weather())
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="rpv2-panel-weather"' in body
    # No Mapbox token in the test env → the lean per-stop fallback list renders.
    assert 'Midway Control' in body
    assert '°F' in body                 # per-stop temperature rendered
    # The full map only mounts when a token + warm cache both exist (covered in
    # test_plan_weather_mapbox); with no token there is no Mapbox on the page.
    assert 'mapbox' not in body.lower()


def test_weather_tab_note_when_no_forecast(client):
    resp = _get(client, '/plan/11?tab=weather', weather=None)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'No forecast cached' in body
    assert 'mapbox' not in body.lower()


# --------------------------------------------------------------------------- #
# Guest safety — no rider PII
# --------------------------------------------------------------------------- #
def test_guest_roster_exposes_local_part_only(client):
    resp = _get(client, '/plan/11', weather=None)
    body = resp.get_data(as_text=True)
    # Local-parts show…
    assert 'alice' in body and 'bob' in body
    # …but never a full email or a google_id, and no '@' from a rider address.
    assert 'alice@' not in body and 'bob@' not in body
    assert 'google_id' not in body


@pytest.mark.parametrize('url', ['/plan/11', '/plan/11?tab=strategies', '/plan/11?tab=weather'])
def test_all_three_tabs_render_200(client, url):
    assert _get(client, url, weather=_cached_weather()).status_code == 200
