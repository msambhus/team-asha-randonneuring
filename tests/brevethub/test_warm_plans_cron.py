"""BrevetHub scheduled route-plan warmer — /cron/warm-brevet-plans.

Pre-fetches + persists real RWGPS ride plans OFF the request path so the guest
/plan page only ever READS a warm cache (and never calls RWGPS live). These tests
pin the contract (mirroring test_cron.py; all RWGPS HTTP mocked, no real DB):
  - auth: Bearer CRON_SECRET required; missing/wrong → 401; secret unset → 500,
  - events with a parseable rwgps_url are warmed; ones without are skipped,
  - it fails SOFT per event (one RWGPS error is counted, others still warm) and is
    idempotent on re-run,
  - the PINNED route is exactly `/cron/warm-brevet-plans` (single `/cron`, no
    double-prefix), and GET works (Vercel cron issues a GET).
"""
from unittest.mock import patch

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/warm-brevet-plans'

# One event WITH a valid RWGPS route, one WITHOUT (unparseable url → skipped).
# start_time rides along (get_route_plan_warm_targets returns it) so the cron can
# clock-type meal breaks.
_TARGETS = [
    {'id': 11, 'rwgps_url': 'https://ridewithgps.com/routes/123', 'start_time': '06:00'},
    {'id': 12, 'rwgps_url': 'https://example.com/not-a-route', 'start_time': None},
]
_BUILT = {'plan': {'name': 'R', 'slug': 'r'}, 'stops': [{'stop_order': 1}]}


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


# --------------------------------------------------------------------------- #
# Auth ladder
# --------------------------------------------------------------------------- #
def test_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_plan_warm_targets') as mock_t:
        resp = client.post(_PATH)
    assert resp.status_code == 401
    mock_t.assert_not_called()


def test_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_plan_warm_targets') as mock_t:
        resp = client.post(_PATH, headers=_auth('nope'))
    assert resp.status_code == 401
    mock_t.assert_not_called()


def test_secret_unset_is_500(app, client):
    app.config['CRON_SECRET'] = None
    resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500


# --------------------------------------------------------------------------- #
# Processing — warm with rwgps_url, skip without
# --------------------------------------------------------------------------- #
def test_warms_events_with_url_skips_without(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_plan_warm_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_route', return_value={'name': 'r'}), \
         patch('brevethub.routes.cron.extract_controls', return_value=[{'x': 1}]), \
         patch('brevethub.routes.cron.build_ride_plan', return_value=_BUILT) as mock_build, \
         patch('brevethub.models.upsert_brevet_route_plan', return_value=1) as mock_up:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['warmed'] == 1        # event 11 counted ONCE (per event, not per variant)
    assert data['skipped'] == 1       # event 12 (unparseable url)
    assert data['considered'] == 2
    # Both variants are built + upserted for the one warmable event, with meals on.
    assert mock_up.call_count == 2
    upsert_variants = {c.kwargs.get('variant') for c in mock_up.call_args_list}
    assert upsert_variants == {'conservative', 'aggressive'}
    build_variants = {c.kwargs.get('profile') for c in mock_build.call_args_list}
    assert build_variants == {'conservative', 'aggressive'}
    assert all(c.kwargs.get('insert_meals') is True for c in mock_build.call_args_list)


def test_get_verb_works(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_plan_warm_targets', return_value=[]):
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['warmed'] == 0


def test_fail_soft_per_event(app, client):
    _with_secret(app)
    targets = [
        {'id': 11, 'rwgps_url': 'https://ridewithgps.com/routes/111'},
        {'id': 22, 'rwgps_url': 'https://ridewithgps.com/routes/222'},
    ]

    def _fetch(route_id, api_key, auth_token):
        if route_id == '111':
            raise Exception('RWGPS 500')
        return {'name': 'ok'}

    with patch('brevethub.models.get_route_plan_warm_targets', return_value=targets), \
         patch('brevethub.routes.cron.fetch_route', side_effect=_fetch), \
         patch('brevethub.routes.cron.extract_controls', return_value=[{'x': 1}]), \
         patch('brevethub.routes.cron.build_ride_plan', return_value=_BUILT), \
         patch('brevethub.models.upsert_brevet_route_plan', return_value=1):
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['warmed'] == 1 and data['failed'] == 1   # one boom, one warmed


def test_club_owned_plan_counted_skipped_not_warmed(app, client):
    """The warm cron must never clobber a club owner's plan: when the upsert reports
    the plan is club-owned (None), the event is counted skipped, not warmed."""
    _with_secret(app)
    targets = [{'id': 11, 'rwgps_url': 'https://ridewithgps.com/routes/123'}]
    with patch('brevethub.models.get_route_plan_warm_targets', return_value=targets), \
         patch('brevethub.routes.cron.fetch_route', return_value={'name': 'r'}), \
         patch('brevethub.routes.cron.extract_controls', return_value=[{'x': 1}]), \
         patch('brevethub.routes.cron.build_ride_plan', return_value=_BUILT), \
         patch('brevethub.models.upsert_brevet_route_plan', return_value=None):
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert data['warmed'] == 0 and data['skipped'] == 1


def test_idempotent_counts_on_rerun(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_plan_warm_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_route', return_value={'name': 'r'}), \
         patch('brevethub.routes.cron.extract_controls', return_value=[{'x': 1}]), \
         patch('brevethub.routes.cron.build_ride_plan', return_value=_BUILT), \
         patch('brevethub.models.upsert_brevet_route_plan', return_value=1):
        first = client.post(_PATH, headers=_auth()).get_json()
        second = client.post(_PATH, headers=_auth()).get_json()
    assert first == second       # stable, idempotent counts


# --------------------------------------------------------------------------- #
# Pinned route path — no double /cron prefix
# --------------------------------------------------------------------------- #
def test_route_path_is_single_prefixed(app):
    rules = [r.rule for r in app.url_map.iter_rules()
             if 'warm-brevet-plans' in r.rule]
    assert rules == ['/cron/warm-brevet-plans'], \
        f"warm-plans cron must be exactly /cron/warm-brevet-plans, got {rules}"
