"""BrevetHub self-service post-ride result — /calendar/<id>/result + set_signup_result.

Deliberate web-parity deviation: the parent web app sets result states via a
club-admin grid; BrevetHub has no admin surface yet, so a rider self-reports the
result of their OWN past ride. Tenant-safe because every mutation binds rider_id, so
a rider can never touch another rider's row (a foreign row reads as not_found → 404).

Two layers of coverage, both without a real DB (repo convention):
  - route: monkeypatch models.set_signup_result — assert the status-only call and the
    sentinel→HTTP-code map, incl. the finish_time contract (client value ignored),
  - model: monkeypatch brevethub.db (query_one/execute) — assert the
    read-then-guarded-write eligibility guard and the finish_time status-only rules.
"""
import os
import re
from unittest.mock import call, patch

from brevethub import models

_RIDER = {'id': 7, 'email': 'rider@example.com', 'club_id': 3}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_PATH = os.path.join(REPO_ROOT, 'brevethub', 'models.py')


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Route: happy path + finish_time contract
# --------------------------------------------------------------------------- #
def test_result_happy_path_going_to_finished(client):
    """Own past going event → 200 and the status is updated; finish_time stays NULL
    (the RUSA cron, not this endpoint, fills it later)."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_signup_result', return_value=('ok', None)) as m:
        resp = client.post('/calendar/11/result', json={'status': 'finished'})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'event_id': 11, 'status': 'finished',
                               'finish_time': None}
    m.assert_called_once_with(7, 11, 'finished')


def test_result_ignores_client_finish_time(client):
    """A client-sent finish_time is ignored — the model is called STATUS-ONLY, and no
    rider value reaches the column."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_signup_result', return_value=('ok', None)) as m:
        resp = client.post('/calendar/11/result',
                           json={'status': 'finished', 'finish_time': '99:99'})
    assert resp.status_code == 200
    assert m.call_args == call(7, 11, 'finished')     # no finish_time arg at all
    assert resp.get_json()['finish_time'] is None


def test_result_reflects_preserved_finish_time(client):
    """A correction among finished reflects an existing RUSA-synced value read-only."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_signup_result', return_value=('ok', '13:37')):
        resp = client.post('/calendar/11/result', json={'status': 'finished'})
    assert resp.status_code == 200
    assert resp.get_json()['finish_time'] == '13:37'


def test_result_post_ride_to_post_ride_allowed(client):
    """A past finished row re-corrected to dnf → 200 (post-ride→post-ride allowed)."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_signup_result', return_value=('ok', None)) as m:
        resp = client.post('/calendar/11/result', json={'status': 'dnf'})
    assert resp.status_code == 200
    m.assert_called_once_with(7, 11, 'dnf')


# --------------------------------------------------------------------------- #
# Route: validation + guard sentinels
# --------------------------------------------------------------------------- #
def test_result_pre_ride_value_rejected_400(client):
    """A pre-ride status in the /result body → 400 before any DB read."""
    _login(client)
    for pre in ('interested', 'maybe', 'going', 'withdraw'):
        with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
             patch('brevethub.models.set_signup_result') as m:
            resp = client.post('/calendar/11/result', json={'status': pre})
        assert resp.status_code == 400, pre
        m.assert_not_called()


def test_result_future_event_409(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_signup_result', return_value=('not_past', None)):
        resp = client.post('/calendar/11/result', json={'status': 'finished'})
    assert resp.status_code == 409


def test_result_ineligible_current_status_409(client):
    """Own past event whose current status is interested/maybe/withdraw → 409."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_signup_result', return_value=('ineligible', None)):
        resp = client.post('/calendar/11/result', json={'status': 'finished'})
    assert resp.status_code == 409


def test_result_requires_login(client):
    with patch('brevethub.models.set_signup_result') as m:
        resp = client.post('/calendar/11/result', json={'status': 'finished'})
    assert resp.status_code == 401
    assert 'login_url' in resp.get_json()
    m.assert_not_called()


# --------------------------------------------------------------------------- #
# Route: cross-rider isolation
# --------------------------------------------------------------------------- #
def test_result_cross_rider_addresses_only_own_id(client):
    """Rider A (id 7) can only ever address their OWN row: the route binds A's id, and
    a row that is not theirs comes back not_found → 404 (absorbs the cross-rider probe)."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_signup_result', return_value=('not_found', None)) as m:
        resp = client.post('/calendar/11/result', json={'status': 'finished'})
    assert resp.status_code == 404
    m.assert_called_once_with(7, 11, 'finished')       # never another rider's id


# --------------------------------------------------------------------------- #
# Model guard: read-then-guarded-write eligibility + finish_time contract
# --------------------------------------------------------------------------- #
def test_model_going_to_finished_is_status_only():
    with patch('brevethub.db.query_one',
               return_value={'status': 'going', 'finish_time': None, 'is_past': True}) as q, \
         patch('brevethub.db.execute', return_value={'finish_time': None}) as e:
        outcome, ft = models.set_signup_result(7, 11, 'finished')
    assert outcome == 'ok'
    assert ft is None
    assert q.call_args[0][1] == (7, 11)                # read is rider-scoped
    sql, params = e.call_args[0][0], e.call_args[0][1]
    assert 'finish_time = NULL' not in sql             # successful branch: status-only
    assert params[0] == 'finished' and params[1] == 7 and params[2] == 11


def test_model_finished_to_dnf_clears_finish_time():
    with patch('brevethub.db.query_one',
               return_value={'status': 'finished', 'finish_time': '13:37', 'is_past': True}), \
         patch('brevethub.db.execute', return_value={'finish_time': None}) as e:
        outcome, ft = models.set_signup_result(7, 11, 'dnf')
    assert outcome == 'ok'
    assert ft is None
    assert 'finish_time = NULL' in e.call_args[0][0]   # a non-finish clears the time


def test_model_finished_to_finished_preserves_finish_time():
    with patch('brevethub.db.query_one',
               return_value={'status': 'finished', 'finish_time': '13:37', 'is_past': True}), \
         patch('brevethub.db.execute', return_value={'finish_time': '13:37'}) as e:
        outcome, ft = models.set_signup_result(7, 11, 'finished')
    assert outcome == 'ok'
    assert ft == '13:37'
    assert 'finish_time = NULL' not in e.call_args[0][0]


def test_model_rejects_pre_ride_current_status():
    for pre in ('interested', 'maybe', 'withdraw'):
        with patch('brevethub.db.query_one',
                   return_value={'status': pre, 'finish_time': None, 'is_past': True}), \
             patch('brevethub.db.execute') as e:
            outcome, ft = models.set_signup_result(7, 11, 'finished')
        assert outcome == 'ineligible', pre
        assert ft is None
        e.assert_not_called()                          # no write on an ineligible row


def test_model_rejects_future_event():
    with patch('brevethub.db.query_one',
               return_value={'status': 'going', 'finish_time': None, 'is_past': False}), \
         patch('brevethub.db.execute') as e:
        outcome, ft = models.set_signup_result(7, 11, 'finished')
    assert outcome == 'not_past'
    e.assert_not_called()


def test_model_not_found_when_no_row():
    with patch('brevethub.db.query_one', return_value=None), \
         patch('brevethub.db.execute') as e:
        outcome, ft = models.set_signup_result(7, 11, 'finished')
    assert outcome == 'not_found'
    e.assert_not_called()


def test_model_concurrent_transition_is_ineligible():
    """The guarded write matching 0 rows (a race changed the status) → ineligible."""
    with patch('brevethub.db.query_one',
               return_value={'status': 'going', 'finish_time': None, 'is_past': True}), \
         patch('brevethub.db.execute', return_value=None):
        outcome, ft = models.set_signup_result(7, 11, 'finished')
    assert outcome == 'ineligible'
    assert ft is None


def test_set_signup_result_source_is_rider_scoped():
    """Static guard: both the read and the guarded write bind rider_id (tenant-safety),
    and the write returns finish_time for the read-only reflection."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    body = re.search(r'def set_signup_result\(.*?\n(?=def )', src, re.DOTALL).group(0)
    assert 's.rider_id = %s' in body
    assert body.count('rider_id = %s') >= 2            # read + guarded write both bound
    assert 'RETURNING finish_time' in body
