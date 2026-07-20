"""BrevetHub auto-finalize cron — /cron/finalize-signups + auto_finalize_past_signups.

Mirrors the parent web app auto-finalize: past-date going sign-ups flip to finished
(interested/maybe/withdraw and future-date rows are untouched). Auth-gated (Bearer
CRON_SECRET); no real DB (models monkeypatched / brevethub.db patched), per the
BrevetHub test convention and the /cron/* test pattern in test_cron.py.
"""
import os
import re
from unittest.mock import patch

from brevethub import models

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/finalize-signups'

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_PATH = os.path.join(REPO_ROOT, 'brevethub', 'models.py')


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


# --------------------------------------------------------------------------- #
# Auth ladder
# --------------------------------------------------------------------------- #
def test_finalize_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.models.auto_finalize_past_signups') as mock_fin:
        resp = client.post(_PATH)   # no Authorization header
    assert resp.status_code == 401
    mock_fin.assert_not_called()


def test_finalize_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.auto_finalize_past_signups') as mock_fin:
        resp = client.post(_PATH, headers=_auth('wrong-secret'))
    assert resp.status_code == 401
    mock_fin.assert_not_called()


def test_finalize_500_when_secret_unset(app, client):
    app.config['CRON_SECRET'] = None
    with patch('brevethub.models.auto_finalize_past_signups') as mock_fin:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500
    mock_fin.assert_not_called()


# --------------------------------------------------------------------------- #
# Behavior
# --------------------------------------------------------------------------- #
def test_finalize_promotes_and_reports_count(app, client):
    _with_secret(app)
    with patch('brevethub.models.auto_finalize_past_signups', return_value=3) as mock_fin:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'finalized': 3}
    mock_fin.assert_called_once_with()


def test_finalize_accepts_get_verb(app, client):
    """Vercel cron issues a GET."""
    _with_secret(app)
    with patch('brevethub.models.auto_finalize_past_signups', return_value=0):
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'finalized': 0}


def test_finalize_degrades_soft_on_db_error(app, client):
    """A DB failure is logged and returned as non-500 JSON so a flaky run never pages."""
    _with_secret(app)
    with patch('brevethub.models.auto_finalize_past_signups', side_effect=RuntimeError('boom')):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is False
    assert body['finalized'] == 0


def test_finalize_route_is_single_cron_prefixed(app, client):
    """Pinned contract: the production URL is exactly /cron/finalize-signups; a double
    /cron prefix (a decorator drift) must 404, so the Vercel-scheduled request lands."""
    _with_secret(app)
    with patch('brevethub.models.auto_finalize_past_signups', return_value=0):
        resp = client.post('/cron/cron/finalize-signups', headers=_auth())
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Model: past-going-only promotion
# --------------------------------------------------------------------------- #
def test_auto_finalize_returns_count():
    with patch('brevethub.db.execute', return_value={'n': 2}) as e:
        finalized = models.auto_finalize_past_signups()
    assert finalized == 2
    sql, params = e.call_args[0][0], e.call_args[0][1]
    assert 'UPDATE rp_event_signup' in sql
    assert 'WHERE status = %s' in sql          # only the going source status
    assert 'date < CURRENT_DATE' in sql        # only past-date events
    assert params == ('finished', 'going')     # promote going -> finished


def test_auto_finalize_zero_when_no_rows():
    with patch('brevethub.db.execute', return_value={'n': 0}):
        assert models.auto_finalize_past_signups() == 0


def test_auto_finalize_source_only_touches_past_going():
    """Static guard on the model SQL: promotes only past-date going rows, nothing
    else (no interested/maybe/withdraw/future in the WHERE)."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    body = re.search(r'def auto_finalize_past_signups\(.*?\n(?=def )', src, re.DOTALL).group(0)
    assert 'UPDATE rp_event_signup' in body
    assert 'WHERE status = %s' in body
    assert 'date < CURRENT_DATE' in body
    assert 'RETURNING id' in body
    assert 'RideStatus.FINISHED.value' in body and 'RideStatus.GOING.value' in body
