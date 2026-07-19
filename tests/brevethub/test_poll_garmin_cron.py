"""BrevetHub Garmin poll cron — /cron/poll-garmin-livetrack.

Mirrors the other BrevetHub cron tests: Bearer CRON_SECRET auth, all HTTP mocked
(the shared Garmin engine is patched at the cron's import site), no real DB. The
security + robustness contracts are pinned:
  - auth ladder: secret unset → 500, missing/wrong header → 401, correct → 200,
  - fail-soft: one rider's fetch raising counts them `failed` and the batch
    continues (the other rider still ingests),
  - idempotent: a re-run whose points are all <= the last stored one inserts 0,
  - skip: a rider with no token/session or no active ride is `skipped`, not polled,
  - the response body is exactly {ok, polled, inserted, skipped, failed},
  - the production URL is exactly /cron/poll-garmin-livetrack and the Vercel GET
    verb works.
"""
from datetime import datetime, timezone
from unittest.mock import patch

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/poll-garmin-livetrack'

# A real LiveTrack share URL so the cron's (real) parse_session derives a session id.
_URL = 'https://livetrack.garmin.com/session/SESS/token/TOK'


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


def _tracked(rider_id=7, ride_id=3, url=_URL, token='TOK'):
    return {'rider_id': rider_id, 'active_ride_id': ride_id,
            'garmin_session_url': url, 'garmin_session_token': token}


def _pt(minute, **tel):
    """A parsed trackpoint (as the shared engine returns) at 06:%M UTC."""
    p = {'lat': 37.0 + minute / 1000.0, 'lng': -122.0, 'speed': None,
         'heart_rate': None, 'power': None, 'cadence': None,
         'recorded_at': datetime(2026, 7, 20, 6, minute, tzinfo=timezone.utc)}
    p.update(tel)
    return p


# --------------------------------------------------------------------------- #
# Auth ladder
# --------------------------------------------------------------------------- #
def test_poll_requires_secret_configured(app, client):
    # CRON_SECRET unset → 500 (misconfiguration), never an unauthenticated poll.
    app.config['CRON_SECRET'] = None
    with patch('brevethub.models.get_enabled_live_tracking_rp') as m:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500
    m.assert_not_called()


def test_poll_rejects_missing_header(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_enabled_live_tracking_rp') as m:
        resp = client.post(_PATH)
    assert resp.status_code == 401
    m.assert_not_called()


def test_poll_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_enabled_live_tracking_rp') as m:
        resp = client.post(_PATH, headers=_auth('wrong'))
    assert resp.status_code == 401
    m.assert_not_called()


def test_poll_accepts_correct_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_enabled_live_tracking_rp', return_value=[]), \
         patch('brevethub.models.purge_old_positions_rp', return_value=0):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {'ok', 'polled', 'inserted', 'skipped', 'failed'}
    assert body == {'ok': True, 'polled': 0, 'inserted': 0, 'skipped': 0, 'failed': 0}


def test_get_verb_works(app, client):
    """Vercel cron issues a GET — the endpoint must accept it (not 405)."""
    _with_secret(app)
    with patch('brevethub.models.get_enabled_live_tracking_rp', return_value=[]), \
         patch('brevethub.models.purge_old_positions_rp', return_value=0):
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Ingestion — pinned points drive inserts, tagged to the rider + ride
# --------------------------------------------------------------------------- #
def test_poll_inserts_fresh_points_with_telemetry(app, client):
    _with_secret(app)
    points = [_pt(0, speed=8.3, heart_rate=142, power=210, cadence=88),
              _pt(1), _pt(2)]
    with patch('brevethub.models.get_enabled_live_tracking_rp', return_value=[_tracked()]), \
         patch('brevethub.routes.cron.fetch_positions', return_value=points), \
         patch('brevethub.models.get_last_position_recorded_at_rp', return_value=None), \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins, \
         patch('brevethub.models.purge_old_positions_rp', return_value=0):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['polled'] == 1 and body['failed'] == 0 and body['skipped'] == 0
    assert body['inserted'] == 3
    # Each insert is tagged with the rider + ride and carries source='garmin'.
    first = ins.call_args_list[0].kwargs
    assert first['rider_id'] == 7 and first['ride_id'] == 3 and first['source'] == 'garmin'
    assert first['speed'] == 8.3 and first['heart_rate'] == 142


def test_poll_is_idempotent(app, client):
    """A re-run whose points are all <= the last stored one inserts nothing."""
    _with_secret(app)
    points = [_pt(0), _pt(1), _pt(2)]
    last = datetime(2026, 7, 20, 6, 2, tzinfo=timezone.utc)  # newest already stored
    with patch('brevethub.models.get_enabled_live_tracking_rp', return_value=[_tracked()]), \
         patch('brevethub.routes.cron.fetch_positions', return_value=points), \
         patch('brevethub.models.get_last_position_recorded_at_rp', return_value=last), \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins, \
         patch('brevethub.models.purge_old_positions_rp', return_value=0):
        resp = client.post(_PATH, headers=_auth())
    assert resp.get_json()['inserted'] == 0
    ins.assert_not_called()


def test_poll_fail_soft_one_bad_session_continues_batch(app, client):
    _with_secret(app)
    good = [_pt(0), _pt(1)]

    def _fetch(token, session_id):
        if session_id == 'BAD':
            raise Exception('session expired')
        return good

    riders = [_tracked(rider_id=7, ride_id=3, url=_URL),
              _tracked(rider_id=9, ride_id=4,
                       url='https://livetrack.garmin.com/session/BAD/token/X')]
    with patch('brevethub.models.get_enabled_live_tracking_rp', return_value=riders), \
         patch('brevethub.routes.cron.fetch_positions', side_effect=_fetch), \
         patch('brevethub.models.get_last_position_recorded_at_rp', return_value=None), \
         patch('brevethub.models.insert_live_position_rp', return_value=True), \
         patch('brevethub.models.purge_old_positions_rp', return_value=0):
        resp = client.post(_PATH, headers=_auth())
    body = resp.get_json()
    assert body['polled'] == 2          # both had a usable session id
    assert body['failed'] == 1          # the BAD one raised
    assert body['inserted'] == 2        # the good rider still ingested
    assert body['ok'] is True


def test_poll_skips_rider_without_ride_or_token(app, client):
    _with_secret(app)
    riders = [_tracked(rider_id=7, ride_id=None),          # no active ride
              _tracked(rider_id=8, token=None)]            # no token
    with patch('brevethub.models.get_enabled_live_tracking_rp', return_value=riders), \
         patch('brevethub.routes.cron.fetch_positions') as fetch, \
         patch('brevethub.models.purge_old_positions_rp', return_value=0):
        resp = client.post(_PATH, headers=_auth())
    body = resp.get_json()
    assert body['skipped'] == 2 and body['polled'] == 0
    fetch.assert_not_called()


# --------------------------------------------------------------------------- #
# Pinned route contract
# --------------------------------------------------------------------------- #
def test_route_is_single_cron_prefixed(app, client):
    """The Vercel-scheduled URL is exactly /cron/poll-garmin-livetrack; a double
    /cron prefix (a decorator/prefix drift) must NOT resolve."""
    _with_secret(app)
    resp = client.post('/cron/cron/poll-garmin-livetrack', headers=_auth())
    assert resp.status_code == 404
