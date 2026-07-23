"""BrevetHub per-ride analysis route — auth, ownership gate, compute-on-action /
cache-on-read, and the rendered breakdown.

Follows the established BrevetHub test pattern: monkeypatch `brevethub.models.*` and
the Strava HTTP helpers, use the `client` fixture, never touch a real DB or network.
First-class contracts (the mission's verification list):
  - guest -> redirect to login on both the list and the detail,
  - the list renders the rider's own recent rides each with an Analyze control
    (a real render-path assertion — proves no missing-filter 500),
  - a Strava outage on the list degrades to a message, never a 500,
  - compute-once-then-cache: POST /compute fetches streams once + upserts; a later
    GET renders the breakdown and makes ZERO Strava calls (fetch mock un-called),
  - ownership gate: POST /compute for a NON-owned activity id -> 404, with NO stream
    fetch and NO upsert (the redteam regression),
  - read owner-only: rider A requesting another rider's id sees the not-analyzed
    state, never the other rider's data.
"""
from unittest.mock import patch

import pytest


_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

_CONN = {'id': 1, 'rider_id': 7, 'strava_athlete_id': 999, 'access_token': 'A',
         'refresh_token': 'R', 'expires_at': 9999999999.0, 'scope': '',
         'stats_cache': None, 'stats_fetched_at': None}

# One owned Strava ride summary (as fetch_activities returns it).
_ACTIVITY = {
    'id': 555, 'type': 'Ride', 'name': 'Morning Loop', 'distance': 50000.0,
    'moving_time': 7200, 'elapsed_time': 7500, 'total_elevation_gain': 600.0,
    'average_speed': 7.0, 'start_date_local': '2026-07-01T08:00:00Z',
}


def _streams():
    """Synthetic streams with a real 150 s stop at index 100, plus HR/power/cadence."""
    time_arr = list(range(400))
    distance = [i * 10 for i in range(400)]          # 10 m/s
    velocity = [5.0] * 400
    for i in range(100, 250):
        velocity[i] = 0.0
    latlng = [[37.0 + i * 0.001, -122.0 - i * 0.001] for i in range(400)]
    return {'time': time_arr, 'distance': distance, 'velocity_smooth': velocity,
            'latlng': latlng, 'heartrate': [140] * 400, 'watts': [180] * 400,
            'cadence': [85] * 400}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Auth — guest is bounced to login on every surface.
# --------------------------------------------------------------------------- #
def test_list_requires_login(client):
    resp = client.get('/analysis')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


def test_detail_requires_login(client):
    resp = client.get('/analysis/555')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


def test_compute_requires_login(client):
    resp = client.post('/analysis/555/compute')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


# --------------------------------------------------------------------------- #
# List — renders the rider's own recent rides with an Analyze control.
# --------------------------------------------------------------------------- #
def test_list_renders_owned_activities(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.models.get_analyzed_activity_ids', return_value=set()), \
         patch('brevethub.models.get_rider_past_results', return_value=[]), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': [], 'rusa_fetched_at': None}), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities', return_value=[_ACTIVITY]):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Morning Loop' in body
    assert '50.0 km' in body              # distance converted to km at the view boundary
    assert 'Stats' in body                # per-row stats control
    assert 'btn-strava' in body           # Strava-colored analyze action
    assert 'powered_by_strava.svg' in body
    assert '/analysis/555' in body        # links to the detail view


def test_list_marks_already_analyzed_rides(client):
    """An already-analyzed ride offers a cache-read 'View analysis' link, not a
    fresh compute (cache-on-read, no needless recompute)."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.models.get_analyzed_activity_ids', return_value={555}), \
         patch('brevethub.models.get_rider_past_results', return_value=[]), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': [], 'rusa_fetched_at': None}), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities', return_value=[_ACTIVITY]):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    assert 'Stats' in resp.get_data(as_text=True)


def test_list_marks_finished_brevets_differently(client):
    _login(client)
    brevet = {'event_id': 11, 'status': 'finished', 'name': 'Morning 50K Brevet',
              'date': '2026-07-01', 'distance_km': 50, 'finish_time': '3:10',
              'region': 'SFR'}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.models.get_analyzed_activity_ids', return_value=set()), \
         patch('brevethub.models.get_rider_past_results', return_value=[brevet]), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': [], 'rusa_fetched_at': None}), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities', return_value=[_ACTIVITY]):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Brevet' in body
    assert 'Morning 50K Brevet' in body
    assert '<tr class="brevet-row">' in body


def test_list_marks_rusa_cache_brevets_differently(client):
    """A rider's official cached RUSA history classifies matching Strava rides as
    brevets even when no local BrevetHub signup/result row exists."""
    _login(client)
    cache_brevet = {'date': '2026-07-01', 'distance_km': 50,
                    'finish_time': '3:10', 'route_name': 'RUSA Cache 50K'}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.models.get_analyzed_activity_ids', return_value=set()), \
         patch('brevethub.models.get_rider_past_results', return_value=[]), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': [cache_brevet], 'rusa_fetched_at': None}), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities', return_value=[_ACTIVITY]):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Brevet' in body
    assert 'RUSA Cache 50K' in body
    assert '<tr class="brevet-row">' in body


def test_list_sorts_latest_to_oldest_across_brevet_and_regular(client):
    _login(client)
    newer_regular = {**_ACTIVITY, 'id': 556, 'name': 'Newer Regular Ride',
                     'distance': 30000.0,
                     'start_date_local': '2026-07-03T08:00:00Z'}
    older_brevet = {**_ACTIVITY, 'name': 'Older Brevet Activity'}
    cache_brevet = {'date': '2026-07-01', 'distance_km': 50,
                    'finish_time': '3:10', 'route_name': 'Older RUSA 50K'}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.models.get_analyzed_activity_ids', return_value=set()), \
         patch('brevethub.models.get_rider_past_results', return_value=[]), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': [cache_brevet], 'rusa_fetched_at': None}), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities',
               return_value=[older_brevet, newer_regular]):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert body.index('Newer Regular Ride') < body.index('Older Brevet Activity')


def test_list_prompts_when_not_connected(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    assert 'Connect Strava' in resp.get_data(as_text=True)


def test_list_degrades_on_strava_outage(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities',
               side_effect=Exception('strava down')):
        resp = client.get('/analysis')
    assert resp.status_code == 200        # never a 500
    assert 'Could not load your Strava activities' in resp.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Compute-on-action, cache-on-read.
# --------------------------------------------------------------------------- #
def test_compute_fetches_once_then_caches(client):
    _login(client)
    captured = {}

    def _fake_upsert(rider_id, activity_id, analysis, compressed_streams=None):
        captured['rider_id'] = rider_id
        captured['activity_id'] = activity_id
        captured['analysis'] = analysis
        captured['compressed'] = compressed_streams

    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities', return_value=[_ACTIVITY]), \
         patch('brevethub.routes.analysis.fetch_activity_streams',
               return_value=_streams()) as mock_streams, \
         patch('brevethub.models.upsert_ride_analysis', side_effect=_fake_upsert) as mock_up:
        resp = client.post('/analysis/555/compute')

    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/analysis/555')
    mock_streams.assert_called_once()
    mock_up.assert_called_once()
    assert captured['rider_id'] == 7 and captured['activity_id'] == 555
    assert captured['compressed'] is not None        # raw streams cached for the map
    analysis = captured['analysis']
    assert analysis['activity']['name'] == 'Morning Loop'
    assert analysis['activity']['distance_km'] == 50.0
    assert analysis['activity']['strava_url'] == 'https://www.strava.com/activities/555'
    assert analysis['stop_count'] == 1               # the 150 s stop detected
    assert analysis['legs'], "expected inter-stop legs"

    # A subsequent detail GET reads the cache and makes ZERO Strava calls. The
    # historical-wind fetch is mocked so the detail view never hits the network.
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride_analysis',
               return_value={'analysis': analysis, 'activity_streams': b'x',
                             'computed_at': None}), \
         patch('brevethub.routes.analysis.fetch_historical_wind',
               return_value=(None, None)), \
         patch('brevethub.routes.analysis.fetch_activity_streams') as mock_streams_get:
        resp = client.get('/analysis/555')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Morning Loop' in body
    assert 'Segments' in body and 'Stops' in body
    assert 'View on Strava' in body
    assert 'https://www.strava.com/activities/555' in body
    assert 'powered_by_strava.svg' in body
    mock_streams_get.assert_not_called()             # cache-on-read: no recompute


# --------------------------------------------------------------------------- #
# Ownership gate (redteam regression) — a non-owned id is rejected with no fetch,
# no write.
# --------------------------------------------------------------------------- #
def test_compute_rejects_non_owned_activity(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=_CONN), \
         patch('brevethub.routes.analysis._valid_access_token', return_value='tok'), \
         patch('brevethub.routes.analysis.fetch_activities', return_value=[_ACTIVITY]), \
         patch('brevethub.routes.analysis.fetch_activity_streams') as mock_streams, \
         patch('brevethub.models.upsert_ride_analysis') as mock_up:
        # 999 is NOT in the rider's own activity list (555).
        resp = client.post('/analysis/999/compute')
    assert resp.status_code == 404
    mock_streams.assert_not_called()                 # no stream fetch for a non-owned id
    mock_up.assert_not_called()                      # no rp_ride_analysis row written


# --------------------------------------------------------------------------- #
# Read owner-only — rider A never sees another rider's cached analysis.
# --------------------------------------------------------------------------- #
def test_detail_owner_scoped_read(client):
    _login(client)  # rider 7
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride_analysis', return_value=None) as mock_get:
        resp = client.get('/analysis/555')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "hasn't been analyzed yet" in body        # not-analyzed state, never other data
    assert 'Stats' in body
    # The read was scoped by the session rider id (7), not the URL alone.
    mock_get.assert_called_once_with(7, 555)


def test_detail_renders_cached_breakdown(client):
    """Render-path proof: a fully-populated cached analysis renders with no missing
    commafy/clean_name filter 500."""
    _login(client)
    sample = {
        'activity': {'name': 'Coastal 200', 'date': '2026-06-20', 'distance_km': 203.4,
                     'elevation_ft': 6800, 'moving_time': '9h 12m',
                     'elapsed_time': '11h 40m', 'avg_speed_kmh': 22.1,
                     'strava_url': 'https://www.strava.com/activities/555'},
        'brevet': {'event_id': 11, 'name': 'Coastal 200 Brevet', 'date': '2026-06-20',
                   'distance_km': 200},
        'plan': {'name': 'Coastal 200 Plan'},
        'comparison': {
            'summary': {'plan_name': 'Coastal 200 Plan', 'plan_distance_km': 200.0,
                        'actual_distance_km': 203.4, 'distance_delta_km': 3.4,
                        'plan_elevation_ft': 6600, 'actual_elevation_ft': 6800,
                        'plan_total_time_min': 720, 'actual_elapsed_time_min': 700,
                        'actual_moving_time_min': 552, 'plan_break_time_min': 60,
                        'actual_stopped_time_min': 148, 'stops_planned': 2,
                        'stops_detected': 2, 'stops_extra': 0},
            'rows': [{'location': 'Control A', 'stop_type': 'control',
                      'distance_miles': 62.1, 'distance_km': 100.0,
                      'plan_segment_min': 270, 'actual_segment_min': 260,
                      'plan_speed_mph': 13.8, 'actual_speed_mph': 14.3,
                      'plan_stop_duration_min': 15, 'actual_stop_duration_min': 18.0,
                      'plan_cum_time_min': 285, 'actual_cum_time_min': 278,
                      'actual_avg_hr': 140, 'actual_avg_watts': 170,
                      'actual_avg_cadence': 84, 'actual_elev_gain_ft': 2100,
                      'cum_time_delta_min': -7, 'is_extra': False}],
            'hr_power': True,
        },
        'summary': {'moving_speed_kmh': 23.4, 'avg_hr': 138, 'max_hr': 171,
                    'avg_watts': 165, 'max_watts': 520},
        'stop_count': 2,
        'stops': [{'distance_km': 100.0, 'duration_min': 18.0, 'lat': 37.5, 'lng': -122.3},
                  {'distance_km': 150.0, 'duration_min': 7.5, 'lat': 37.7, 'lng': -122.1}],
        'legs': [{'to_km': 100.0, 'distance_km': 100.0, 'riding_time': '4h 30m',
                  'speed_kmh': 22.2, 'avg_hr': 140, 'avg_watts': 170, 'np_watts': 178,
                  'avg_cadence': 84, 'grade_pct': 1.2, 'climb_ft_per_mi': 45}],
        'map': {'track': [[37.5, -122.3], [37.6, -122.4]],
                'bounds': [[37.5, -122.4], [37.6, -122.3]]},
    }
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride_analysis',
               return_value={'analysis': sample, 'activity_streams': b'x',
                             'computed_at': None}), \
         patch('brevethub.routes.analysis.fetch_historical_wind',
               return_value=(None, None)):
        resp = client.get('/analysis/555')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Coastal 200' in body
    assert '203.4 km' in body
    assert '14.3 mph' in body            # actual per-leg speed in brevet comparison
    assert '84 rpm' in body              # per-leg cadence
    assert '170 W' in body               # per-leg power
    assert '2100 ft' in body             # per-leg climb
    assert '18.0 min' in body            # stop duration
    assert 'analysis-map' in body        # the map container renders when GPS is present
    assert 'Plan vs Actual Stats' in body
    assert 'View on Strava' in body
    assert 'https://www.strava.com/activities/555' in body
    assert 'powered_by_strava.svg' in body
    assert 'Plan vs Actual Stats' in body
    assert 'Route Map' in body
    assert 'Ride Timeline' in body
    assert 'Color key:' in body
    assert 'Enroute Stops' in body
    assert '<th style="text-align:right;">Clock</th>' in body
    assert '<th style="text-align:right;">Bank</th>' in body
    assert 'Add note for this segment' in body
    assert 'Brevet stats' in body
    assert 'Control A' in body
    assert '13.8 mph' in body and '14.3 mph' in body
