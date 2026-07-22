"""Team Asha /api/cron/warm-plan-elevation — caches the RWGPS elevation track for EVERY
route referenced by a ride_plan (past and upcoming) into the route-keyed
route_geometry_cache, so the rpv2 plan-page gradient profile renders for any plan without
a live RWGPS fetch on the request path (TA-237)."""
from datetime import datetime, timezone
from unittest.mock import patch

CRON_SECRET = 'test-cron-secret'
_PATH = '/api/cron/warm-plan-elevation'

# One plan carries its route in the url, the other in the numeric rwgps_route_id column —
# the cron enumerates both.
_PLANS = [
    {'rwgps_url': 'https://ridewithgps.com/routes/555', 'rwgps_url_team': None,
     'rwgps_route_id': None},
    {'rwgps_url': None, 'rwgps_url_team': None, 'rwgps_route_id': 777},
]
_TRACK = {'track_points': [
    {'x': -121.0, 'y': 44.0, 'd': 0, 'e': 100.0},
    {'x': -121.2, 'y': 44.1, 'd': 100000, 'e': 400.0},
]}


def _auth():
    return {'Authorization': f'Bearer {CRON_SECRET}'}


def test_warm_plan_elevation_requires_auth(app, client):
    app.config['CRON_SECRET'] = CRON_SECRET
    resp = client.post(_PATH)  # no Authorization header
    assert resp.status_code == 401


def test_warm_plan_elevation_warms_all_plan_routes(app, client):
    app.config['CRON_SECRET'] = CRON_SECRET
    saved = []
    with patch('models.get_all_ride_plans', return_value=_PLANS), \
         patch('models.get_route_geometry_freshness', return_value=None), \
         patch('services.rwgps.fetch_route', return_value=_TRACK), \
         patch('models.upsert_route_geometry',
               side_effect=lambda rid, et, **k: saved.append(rid)):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['warmed'] == 2 and body['failed'] == 0
    # Route id resolved from BOTH the url (555) and the numeric column (777).
    assert set(saved) == {'555', '777'}


def test_warm_plan_elevation_skips_fresh(app, client):
    app.config['CRON_SECRET'] = CRON_SECRET
    with patch('models.get_all_ride_plans', return_value=_PLANS), \
         patch('models.get_route_geometry_freshness',
               return_value=datetime.now(timezone.utc)), \
         patch('services.rwgps.fetch_route') as mfetch, \
         patch('models.upsert_route_geometry') as mup:
        resp = client.post(_PATH, headers=_auth())
    body = resp.get_json()
    assert body['skipped'] == 2 and body['warmed'] == 0
    mfetch.assert_not_called()
    mup.assert_not_called()


def test_warm_plan_elevation_force_bypasses_fresh(app, client):
    app.config['CRON_SECRET'] = CRON_SECRET
    with patch('models.get_all_ride_plans', return_value=_PLANS), \
         patch('models.get_route_geometry_freshness',
               return_value=datetime.now(timezone.utc)), \
         patch('services.rwgps.fetch_route', return_value=_TRACK), \
         patch('models.upsert_route_geometry'):
        resp = client.post(_PATH + '?force=1', headers=_auth())
    assert resp.get_json()['warmed'] == 2


def test_warm_plan_elevation_fail_soft(app, client):
    app.config['CRON_SECRET'] = CRON_SECRET
    with patch('models.get_all_ride_plans', return_value=_PLANS), \
         patch('models.get_route_geometry_freshness', return_value=None), \
         patch('services.rwgps.fetch_route', side_effect=Exception('rwgps down')), \
         patch('models.upsert_route_geometry') as mup:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['failed'] == 2 and body['warmed'] == 0
    mup.assert_not_called()
