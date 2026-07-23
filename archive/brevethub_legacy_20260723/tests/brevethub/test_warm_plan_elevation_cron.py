"""BrevetHub /cron/warm-plan-elevation — caches the RWGPS elevation track for EVERY route
referenced by an rp_brevet_route_plan (past and upcoming) into the route-keyed
rp_route_geometry_cache, so the guest rpv2 /plan gradient profile renders for any plan
without a live RWGPS fetch on the request path."""
from datetime import datetime, timezone
from unittest.mock import patch

_SECRET = 'test-cron-secret'
_PATH = '/cron/warm-plan-elevation'

# One plan carries the numeric route id, the other only the url — the cron resolves both.
_PLANS = [
    {'rwgps_route_id': 555, 'rwgps_url': 'https://ridewithgps.com/routes/555'},
    {'rwgps_route_id': None, 'rwgps_url': 'https://ridewithgps.com/routes/777'},
]
_ROUTE = {'track_points': [
    {'x': -121.0, 'y': 44.0, 'd': 0, 'e': 100.0},
    {'x': -121.2, 'y': 44.1, 'd': 100000, 'e': 400.0},
]}


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def test_warm_plan_elevation_requires_auth(app, client):
    app.config['CRON_SECRET'] = _SECRET
    resp = client.post(_PATH)  # no Authorization header
    assert resp.status_code == 401


def test_warm_plan_elevation_rejects_wrong_secret(app, client):
    app.config['CRON_SECRET'] = _SECRET
    resp = client.post(_PATH, headers=_auth('nope'))
    assert resp.status_code == 401


def test_warm_plan_elevation_warms_all_plan_routes(app, client):
    app.config['CRON_SECRET'] = _SECRET
    app.config['RWGPS_API_KEY'] = 'k'
    app.config['RWGPS_AUTH_TOKEN'] = 't'
    saved = []
    with patch('brevethub.models.get_brevet_route_plan_route_ids', return_value=_PLANS), \
         patch('brevethub.models.get_rp_route_geometry_freshness', return_value=None), \
         patch('brevethub.routes.cron.fetch_route', return_value=_ROUTE), \
         patch('brevethub.models.upsert_rp_route_geometry',
               side_effect=lambda rid, et, **k: saved.append(rid)):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {'ok': True, 'warmed': 2, 'skipped': 0, 'failed': 0, 'considered': 2}
    assert set(saved) == {'555', '777'}


def test_warm_plan_elevation_skips_fresh(app, client):
    app.config['CRON_SECRET'] = _SECRET
    with patch('brevethub.models.get_brevet_route_plan_route_ids', return_value=_PLANS), \
         patch('brevethub.models.get_rp_route_geometry_freshness',
               return_value=datetime.now(timezone.utc)), \
         patch('brevethub.routes.cron.fetch_route') as mfetch, \
         patch('brevethub.models.upsert_rp_route_geometry') as mup:
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert data['skipped'] == 2 and data['warmed'] == 0
    mfetch.assert_not_called()
    mup.assert_not_called()


def test_warm_plan_elevation_fail_soft(app, client):
    app.config['CRON_SECRET'] = _SECRET
    with patch('brevethub.models.get_brevet_route_plan_route_ids', return_value=_PLANS), \
         patch('brevethub.models.get_rp_route_geometry_freshness', return_value=None), \
         patch('brevethub.routes.cron.fetch_route', side_effect=Exception('rwgps down')), \
         patch('brevethub.models.upsert_rp_route_geometry') as mup:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['failed'] == 2 and data['warmed'] == 0
    mup.assert_not_called()
