"""BrevetHub RUSA finish-time backfill — /cron/sync-rusa-results + the matcher.

Back-fills official RUSA finish times onto finished sign-ups. finish_time is the one
column RUSA owns, and this cron is its SOLE real-value writer. It prefers the
already-cached rusa_cache and only falls back to a live shared fetch (memoized per
rusa_id). Matches the parent web app sync_rusa_finish_times window (+-10 days /
+-20 km, or both >= 1000 km). All RUSA HTTP mocked; no real DB (models patched).
"""
import os
import re
from datetime import date
from unittest.mock import patch

from brevethub import models
from brevethub.routes import cron

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/sync-rusa-results'

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_PATH = os.path.join(REPO_ROOT, 'brevethub', 'models.py')


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


# A cached target (rusa_cache present) and a cache-empty target (live fallback).
_TARGET_CACHED = {
    'id': 1, 'rider_id': 7, 'event_id': 11, 'rusa_id': '12345',
    'rusa_cache': [{'date': '2026-05-10', 'distance_km': 200,
                    'finish_time': '13:37', 'route_name': 'Foo 200'}],
    'date': '2026-05-12', 'distance_km': 200, 'name': 'Foo 200',
}
_TARGET_NOCACHE = {
    'id': 2, 'rider_id': 8, 'event_id': 12, 'rusa_id': '67890',
    'rusa_cache': None, 'date': '2026-05-12', 'distance_km': 300, 'name': 'Bar 300',
}


# --------------------------------------------------------------------------- #
# Matcher (pure) — date/distance window, both cache + live shapes
# --------------------------------------------------------------------------- #
def test_match_cache_shape_iso_dates():
    results = [{'date': '2026-05-10', 'distance_km': 200, 'finish_time': '13:37'}]
    assert cron._match_rusa_finish_time('2026-05-12', 200, results) == '13:37'


def test_match_live_shape_date_objects():
    results = [{'date': date(2026, 5, 10), 'distance_km': 200, 'finish_time': '13:37'}]
    assert cron._match_rusa_finish_time(date(2026, 5, 10), 200, results) == '13:37'


def test_match_date_out_of_window():
    results = [{'date': '2026-05-10', 'distance_km': 200, 'finish_time': '13:37'}]
    assert cron._match_rusa_finish_time('2026-06-10', 200, results) is None


def test_match_distance_out_of_window():
    results = [{'date': '2026-05-10', 'distance_km': 300, 'finish_time': '13:37'}]
    assert cron._match_rusa_finish_time('2026-05-10', 200, results) is None


def test_match_long_brevet_both_over_1000():
    """A 200 km distance gap is allowed when both sides are >= 1000 km."""
    results = [{'date': '2026-05-10', 'distance_km': 1200, 'finish_time': '90:00'}]
    assert cron._match_rusa_finish_time('2026-05-11', 1000, results) == '90:00'


def test_match_blank_finish_time_is_none():
    results = [{'date': '2026-05-10', 'distance_km': 200, 'finish_time': ''}]
    assert cron._match_rusa_finish_time('2026-05-10', 200, results) is None


def test_match_no_results():
    assert cron._match_rusa_finish_time('2026-05-10', 200, []) is None
    assert cron._match_rusa_finish_time('2026-05-10', 200, None) is None


def test_coerce_date_variants():
    assert cron._coerce_date('2026-05-10') == date(2026, 5, 10)
    assert cron._coerce_date(date(2026, 5, 10)) == date(2026, 5, 10)
    assert cron._coerce_date(None) is None
    assert cron._coerce_date('garbage') is None


# --------------------------------------------------------------------------- #
# Cron route — auth, cache-reuse vs live fallback, soft failure, pinned route
# --------------------------------------------------------------------------- #
def test_sync_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_signups_needing_finish_time') as mock_targets:
        resp = client.post(_PATH)
    assert resp.status_code == 401
    mock_targets.assert_not_called()


def test_sync_500_when_secret_unset(app, client):
    app.config['CRON_SECRET'] = None
    with patch('brevethub.models.get_signups_needing_finish_time') as mock_targets:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500
    mock_targets.assert_not_called()


def test_sync_uses_cache_without_live_fetch(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_signups_needing_finish_time', return_value=[_TARGET_CACHED]), \
         patch('brevethub.routes.cron.fetch_rider_results') as mock_fetch, \
         patch('brevethub.models.set_signup_finish_time', return_value=True) as mock_set:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'synced': 1, 'considered': 1}
    mock_fetch.assert_not_called()                     # cache-reuse path (no scrape)
    mock_set.assert_called_once_with(1, '13:37')


def test_sync_falls_back_to_live_fetch_when_cache_empty(app, client):
    _with_secret(app)
    live = [{'date': date(2026, 5, 12), 'distance_km': 300, 'finish_time': '20:00'}]
    with patch('brevethub.models.get_signups_needing_finish_time', return_value=[_TARGET_NOCACHE]), \
         patch('brevethub.routes.cron.fetch_rider_results', return_value=live) as mock_fetch, \
         patch('brevethub.models.set_signup_finish_time', return_value=True) as mock_set:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['synced'] == 1
    mock_fetch.assert_called_once_with('67890')        # cache empty -> one live fetch
    mock_set.assert_called_once_with(2, '20:00')


def test_sync_out_of_window_writes_nothing(app, client):
    _with_secret(app)
    target = dict(_TARGET_CACHED)
    target['date'] = '2026-09-01'                      # far from the cached 2026-05-10
    with patch('brevethub.models.get_signups_needing_finish_time', return_value=[target]), \
         patch('brevethub.routes.cron.fetch_rider_results') as mock_fetch, \
         patch('brevethub.models.set_signup_finish_time') as mock_set:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'synced': 0, 'considered': 1}
    mock_set.assert_not_called()


def test_sync_soft_fail_on_target_load(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_signups_needing_finish_time',
               side_effect=RuntimeError('boom')):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is False


def test_sync_live_fetch_failure_is_soft(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_signups_needing_finish_time', return_value=[_TARGET_NOCACHE]), \
         patch('brevethub.routes.cron.fetch_rider_results', side_effect=RuntimeError('rusa down')), \
         patch('brevethub.models.set_signup_finish_time') as mock_set:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'synced': 0, 'considered': 1}
    mock_set.assert_not_called()


def test_sync_accepts_get_verb(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_signups_needing_finish_time', return_value=[]):
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'synced': 0, 'considered': 0}


def test_sync_route_is_single_cron_prefixed(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_signups_needing_finish_time', return_value=[]):
        resp = client.post('/cron/cron/sync-rusa-results', headers=_auth())
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Model: sole finish_time writer + target reader
# --------------------------------------------------------------------------- #
def test_set_signup_finish_time_writes_and_returns_true():
    with patch('brevethub.db.execute', return_value={'id': 1}) as e:
        assert models.set_signup_finish_time(1, '13:37') is True
    sql, params = e.call_args[0][0], e.call_args[0][1]
    assert 'UPDATE rp_event_signup' in sql
    assert "finish_time IS NULL OR finish_time = ''" in sql   # never overwrites
    assert 'RETURNING id' in sql
    assert params == ('13:37', 1, 'finished')                 # only a finished row


def test_set_signup_finish_time_false_when_no_row():
    with patch('brevethub.db.execute', return_value=None):
        assert models.set_signup_finish_time(1, '13:37') is False


def test_get_signups_needing_finish_time_source():
    """Static guard: the reader selects only finished rows still missing a time whose
    rider has a rusa_id, and carries the cached history for the reuse path."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    body = re.search(r'def get_signups_needing_finish_time\(.*?\n(?=def )',
                     src, re.DOTALL).group(0)
    assert 'WHERE s.status = %s' in body
    assert "s.finish_time IS NULL OR s.finish_time = ''" in body
    assert 'r.rusa_id IS NOT NULL' in body
    assert 'r.rusa_cache' in body                              # reuse-path column
    assert 'RideStatus.FINISHED.value' in body
