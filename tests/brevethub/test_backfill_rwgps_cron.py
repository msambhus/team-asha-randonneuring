"""BrevetHub RWGPS-URL backfill cron — /cron/backfill-rwgps-urls.

Populates the NULL rwgps_url column on rp_brevet_event OFF the request path (the
/calendar seed scrapes with fetch_rwgps=False, so rows land with a rusa_route_id but
no rwgps_url and /cron/warm-brevet-plans has nothing to warm). These tests pin the
contract (mirroring test_warm_plans_cron.py; all RUSA HTTP mocked, no real DB):
  - auth: Bearer CRON_SECRET required; missing/wrong -> 401; secret unset -> 500,
  - the batch query is LIMIT-bounded (BATCH_SIZE), NULL-and-route-id filtered, and
    ordered upcoming-first,
  - per-row success scrapes get_rwgps_url_from_route(rusa_route_id) and writes the URL,
  - it fails SOFT per row (a raise or a None result leaves that row NULL, batch goes on)
    and is idempotent on re-run,
  - the JSON response is exactly {ok, considered, filled, still_null, remaining},
  - the model reader/writer SQL carries the NULL filter, LIMIT, upcoming-first order,
    and single-column NULL-guarded write,
  - the PINNED route is exactly `/cron/backfill-rwgps-urls` (single `/cron`, no
    double-prefix), GET works (Vercel cron issues a GET), and vercel.json schedules it
    before /cron/warm-brevet-plans.
"""
import json
import os
from unittest.mock import patch

from brevethub import db, models

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/backfill-rwgps-urls'


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


# --------------------------------------------------------------------------- #
# Auth ladder
# --------------------------------------------------------------------------- #
def test_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_events_needing_rwgps_url') as mock_r:
        resp = client.post(_PATH)
    assert resp.status_code == 401
    mock_r.assert_not_called()


def test_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_events_needing_rwgps_url') as mock_r:
        resp = client.post(_PATH, headers=_auth('nope'))
    assert resp.status_code == 401
    mock_r.assert_not_called()


def test_secret_unset_is_500(app, client):
    app.config['CRON_SECRET'] = None
    resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500


def test_get_and_post_both_reach_handler(app, client):
    _with_secret(app)
    for verb in (client.get, client.post):
        with patch('brevethub.models.get_events_needing_rwgps_url', return_value=[]), \
             patch('brevethub.models.set_event_rwgps_url'), \
             patch('brevethub.routes.cron.get_rwgps_url_from_route'):
            resp = verb(_PATH, headers=_auth())
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True


# --------------------------------------------------------------------------- #
# Batch query — LIMIT-bounded per run
# --------------------------------------------------------------------------- #
def test_batch_query_is_limit_bounded(app, client):
    _with_secret(app)
    from brevethub.routes.cron import BATCH_SIZE
    with patch('brevethub.models.get_events_needing_rwgps_url', return_value=[]) as mock_r, \
         patch('brevethub.models.set_event_rwgps_url'), \
         patch('brevethub.routes.cron.get_rwgps_url_from_route'):
        client.get(_PATH, headers=_auth())
    # The reader is called with the module BATCH_SIZE cap so a run never scrapes the
    # whole backlog (bounded to stay inside the serverless budget).
    assert mock_r.call_args_list[0].args == (BATCH_SIZE,)
    assert isinstance(BATCH_SIZE, int) and BATCH_SIZE > 0


# --------------------------------------------------------------------------- #
# Per-row success — scrape the route id, write the returned URL
# --------------------------------------------------------------------------- #
def test_per_row_success_fills(app, client):
    _with_secret(app)
    batch = [{'id': 5, 'rusa_route_id': '900'}]
    url = 'https://ridewithgps.com/routes/42'
    with patch('brevethub.models.get_events_needing_rwgps_url', side_effect=[batch, []]), \
         patch('brevethub.routes.cron.get_rwgps_url_from_route', return_value=url) as mock_scrape, \
         patch('brevethub.models.set_event_rwgps_url', return_value=True) as mock_set:
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['ok'] is True
    assert data['considered'] == 1
    assert data['filled'] == 1
    assert data['still_null'] == 0
    assert data['remaining'] == 0
    mock_scrape.assert_called_once_with('900')
    mock_set.assert_called_once_with(5, url)


# --------------------------------------------------------------------------- #
# Fail-soft per row — a raise and a None both leave the row NULL, batch continues
# --------------------------------------------------------------------------- #
def test_fail_soft_per_row(app, client):
    _with_secret(app)
    batch = [
        {'id': 1, 'rusa_route_id': 'boom'},   # scrape raises
        {'id': 2, 'rusa_route_id': 'none'},   # scrape returns None
        {'id': 3, 'rusa_route_id': 'ok'},     # scrape succeeds
    ]
    still = [
        {'id': 1, 'rusa_route_id': 'boom'},
        {'id': 2, 'rusa_route_id': 'none'},
    ]
    url = 'https://ridewithgps.com/routes/3'

    def _scrape(route_id, **kwargs):
        if route_id == 'boom':
            raise Exception('RUSA 500')
        if route_id == 'none':
            return None
        return url

    with patch('brevethub.models.get_events_needing_rwgps_url', side_effect=[batch, still]), \
         patch('brevethub.routes.cron.get_rwgps_url_from_route', side_effect=_scrape), \
         patch('brevethub.models.set_event_rwgps_url', return_value=True) as mock_set:
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['considered'] == 3
    assert data['filled'] == 1
    assert data['still_null'] == 2
    assert data['remaining'] == 2
    # The writer runs ONLY for the successful row — the raise and the None never write.
    mock_set.assert_called_once_with(3, url)


# --------------------------------------------------------------------------- #
# JSON response shape
# --------------------------------------------------------------------------- #
def test_json_response_shape(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_events_needing_rwgps_url', return_value=[]), \
         patch('brevethub.models.set_event_rwgps_url'), \
         patch('brevethub.routes.cron.get_rwgps_url_from_route'):
        resp = client.get(_PATH, headers=_auth())
    data = resp.get_json()
    assert set(data.keys()) == {'ok', 'considered', 'filled', 'still_null', 'remaining'}
    assert data['ok'] is True
    assert all(isinstance(data[k], int)
               for k in ('considered', 'filled', 'still_null', 'remaining'))


def test_target_load_failure_is_soft(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_events_needing_rwgps_url',
               side_effect=Exception('db down')):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is False
    assert set(data.keys()) >= {'ok', 'considered', 'filled', 'still_null', 'remaining'}


# --------------------------------------------------------------------------- #
# Idempotence — a re-run yields stable counts; nothing re-writes a filled row
# --------------------------------------------------------------------------- #
def test_idempotent_counts_on_rerun(app, client):
    _with_secret(app)
    batch = [{'id': 1, 'rusa_route_id': '10'}]
    # A route page with no RideWithGPS link returns None, so nothing fills and the row
    # stays NULL — counts are identical across runs.
    with patch('brevethub.models.get_events_needing_rwgps_url', return_value=batch), \
         patch('brevethub.routes.cron.get_rwgps_url_from_route', return_value=None), \
         patch('brevethub.models.set_event_rwgps_url', return_value=True) as mock_set:
        first = client.post(_PATH, headers=_auth()).get_json()
        second = client.post(_PATH, headers=_auth()).get_json()
    assert first == second
    mock_set.assert_not_called()


# --------------------------------------------------------------------------- #
# Model reader / writer SQL contract (idempotence + rp-only guarantees)
# --------------------------------------------------------------------------- #
def test_reader_sql_contract(monkeypatch):
    captured = {}

    def fake_query(sql, params=None, **kwargs):
        captured['sql'] = sql
        captured['params'] = params
        return [{'id': 1, 'rusa_route_id': '9'}]

    monkeypatch.setattr(db, 'query', fake_query)
    rows = models.get_events_needing_rwgps_url(25)
    sql = captured['sql']
    assert 'FROM rp_brevet_event' in sql
    assert 'rwgps_url IS NULL' in sql               # idempotent: only unfilled rows
    assert 'rusa_route_id IS NOT NULL' in sql       # need a route id to scrape
    assert '(date >= CURRENT_DATE) DESC' in sql     # upcoming/future events first
    assert 'LIMIT %s' in sql                        # bounded per run
    assert captured['params'] == (25,)
    assert rows == [{'id': 1, 'rusa_route_id': '9'}]


def test_writer_sql_contract(monkeypatch):
    captured = {}

    def fake_execute(sql, params=None, returning=False, **kwargs):
        captured['sql'] = sql
        captured['params'] = params
        captured['returning'] = returning
        return {'id': 7}

    monkeypatch.setattr(db, 'execute', fake_execute)
    url = 'https://ridewithgps.com/routes/5'
    result = models.set_event_rwgps_url(7, url)
    sql = captured['sql']
    assert 'UPDATE rp_brevet_event' in sql
    assert 'SET rwgps_url = %s' in sql              # single-column write
    assert 'rwgps_url IS NULL' in sql               # NULL guard: never overwrite
    assert 'RETURNING id' in sql
    assert captured['returning'] is True
    assert captured['params'] == (url, 7)
    assert result is True


def test_writer_returns_false_when_guard_blocks(monkeypatch):
    # RETURNING yields no row when the WHERE rwgps_url IS NULL guard blocks the update.
    monkeypatch.setattr(db, 'execute', lambda *a, **k: None)
    assert models.set_event_rwgps_url(7, 'https://ridewithgps.com/routes/5') is False


# --------------------------------------------------------------------------- #
# Pinned route path — no double /cron prefix
# --------------------------------------------------------------------------- #
def test_route_path_is_single_prefixed(app):
    rules = [r.rule for r in app.url_map.iter_rules()
             if 'backfill-rwgps-urls' in r.rule]
    assert rules == ['/cron/backfill-rwgps-urls'], \
        f"backfill cron must be exactly /cron/backfill-rwgps-urls, got {rules}"


# --------------------------------------------------------------------------- #
# vercel.json — scheduled daily before the warm-brevet-plans slot
# --------------------------------------------------------------------------- #
def test_vercel_cron_registered_before_warm_plans():
    with open(os.path.join(REPO_ROOT, 'brevethub', 'vercel.json')) as fh:
        cfg = json.load(fh)
    crons = cfg['crons']
    backfill = [c for c in crons if c['path'] == '/cron/backfill-rwgps-urls']
    assert len(backfill) == 1, 'exactly one backfill-rwgps-urls cron entry expected'

    def _minute_of_day(schedule):
        minute, hour = schedule.split()[:2]
        return int(hour) * 60 + int(minute)

    warm = [c for c in crons if c['path'] == '/cron/warm-brevet-plans'][0]
    # A URL filled in the morning must be warmable into a plan the SAME day.
    assert _minute_of_day(backfill[0]['schedule']) < _minute_of_day(warm['schedule'])
