"""Tests for passwordless email OTP login: services/otp_service.py,
services/email_service.py, and the /api/auth/otp/* endpoints.

Model lookups, mail sending and Resend HTTP are all mocked — no DB or network.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import auth as auth_mod
from services import otp_service


# ── otp_service unit ──────────────────────────────────────────────────────

def test_generate_code_is_six_digits():
    for _ in range(50):
        code = otp_service.generate_code()
        assert len(code) == 6 and code.isdigit()


def test_verify_code_round_trip():
    h = otp_service.hash_code('483920')
    assert otp_service.verify_code('483920', h) is True
    assert otp_service.verify_code('000000', h) is False


@pytest.mark.parametrize('code,h', [('', 'x'), ('123456', ''), (None, 'x'), ('123456', None)])
def test_verify_code_rejects_empty(code, h):
    assert otp_service.verify_code(code, h) is False


def test_hash_link_token_is_deterministic_sha256():
    t = otp_service.new_link_token()
    assert otp_service.hash_link_token(t) == otp_service.hash_link_token(t)
    assert len(otp_service.hash_link_token(t)) == 64          # sha256 hex
    assert otp_service.hash_link_token(t) != otp_service.hash_link_token(t + 'x')


def test_link_tokens_are_unique_and_urlsafe():
    tokens = {otp_service.new_link_token() for _ in range(20)}
    assert len(tokens) == 20                                   # no collisions
    for t in tokens:
        assert '"' not in t and "'" not in t and '<' not in t  # safe to embed


def test_magic_and_deep_link_formats(app):
    with app.app_context():
        assert otp_service.magic_url('tok123').endswith('/api/auth/otp/magic?token=tok123')
        assert otp_service.app_deep_link('tok123') == 'teamasha://auth/otp?token=tok123'


# ── email_service unit ────────────────────────────────────────────────────

def test_send_email_no_key_is_noop(app, monkeypatch):
    monkeypatch.delenv('RESEND_API_KEY', raising=False)
    from services import email_service
    with app.app_context(), patch('services.email_service.requests.post') as mock_post:
        assert email_service.send_email('a@b.com', 'Subj', '<p>hi</p>') is False
    mock_post.assert_not_called()                              # never calls Resend without a key


def test_send_email_success(app, monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY', 're_test')
    from services import email_service
    with app.app_context(), patch('services.email_service.requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        assert email_service.send_email('a@b.com', 'Subj', '<p>hi</p>', text='hi') is True
    _, kwargs = mock_post.call_args
    assert kwargs['headers']['Authorization'] == 'Bearer re_test'
    assert kwargs['json']['to'] == ['a@b.com'] and kwargs['timeout'] == email_service._TIMEOUT


def test_send_email_http_error_is_false(app, monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY', 're_test')
    from services import email_service
    with app.app_context(), patch('services.email_service.requests.post') as mock_post:
        mock_post.return_value.status_code = 422
        mock_post.return_value.text = 'bad domain'
        assert email_service.send_email('a@b.com', 'Subj', '<p>hi</p>') is False


def test_send_email_network_error_is_false(app, monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY', 're_test')
    import requests
    from services import email_service
    with app.app_context(), patch('services.email_service.requests.post',
                                  side_effect=requests.Timeout('slow')):
        assert email_service.send_email('a@b.com', 'Subj', '<p>hi</p>') is False


# ── POST /api/auth/otp/request ────────────────────────────────────────────

def test_otp_request_invalid_email_400(client):
    resp = client.post('/api/auth/otp/request', json={'email': 'not-an-email'})
    assert resp.status_code == 400


def test_otp_request_happy_path_sends_and_stores(client):
    with patch('models.count_recent_otps_by_ip', return_value=0), \
         patch('models.count_recent_otps', return_value=0), \
         patch('models.get_active_otp_by_identifier', return_value=None), \
         patch('models.invalidate_active_otps', return_value=0) as mock_invalidate, \
         patch('models.create_otp', return_value=1) as mock_store, \
         patch('services.otp_service.send_otp_email', return_value=True) as mock_send:
        resp = client.post('/api/auth/otp/request', json={'email': 'Rider@Example.com'})
    assert resp.status_code == 200
    # Stored under the lowercased email; the emailed code is never in the response.
    assert mock_store.call_args[0][0] == 'rider@example.com'
    assert 'code' not in resp.get_json()
    # Prior live codes are superseded before a new one is issued (per-email lockout).
    mock_invalidate.assert_called_once_with('rider@example.com')
    email_arg, code_arg, link_arg = mock_send.call_args[0]
    assert email_arg == 'rider@example.com' and len(code_arg) == 6


def test_otp_request_non_enumerating(client):
    """Same generic response whether or not an account exists — /request never
    looks up the account (find-or-create happens at verify)."""
    bodies = []
    for addr in ('known@example.com', 'nobody@example.com'):
        with patch('models.count_recent_otps_by_ip', return_value=0), \
             patch('models.count_recent_otps', return_value=0), \
             patch('models.get_active_otp_by_identifier', return_value=None), \
             patch('models.invalidate_active_otps', return_value=0), \
             patch('models.create_otp', return_value=1), \
             patch('services.otp_service.send_otp_email', return_value=True):
            resp = client.post('/api/auth/otp/request', json={'email': addr})
        bodies.append((resp.status_code, resp.get_json()))
    assert bodies[0] == bodies[1]                              # indistinguishable


def test_otp_request_ip_cap_429(client):
    """A single source can't email-bomb many addresses: the per-IP cap fires
    before the per-email checks."""
    with patch('models.count_recent_otps_by_ip', return_value=otp_service.IP_MAX_PER_HOUR), \
         patch('models.create_otp') as mock_store, \
         patch('services.otp_service.send_otp_email') as mock_send:
        resp = client.post('/api/auth/otp/request', json={'email': 'victim@example.com'})
    assert resp.status_code == 429
    mock_store.assert_not_called()
    mock_send.assert_not_called()


def test_otp_request_hourly_cap_429(client):
    with patch('models.count_recent_otps_by_ip', return_value=0), \
         patch('models.count_recent_otps', return_value=otp_service.MAX_PER_HOUR), \
         patch('services.otp_service.send_otp_email') as mock_send:
        resp = client.post('/api/auth/otp/request', json={'email': 'r@example.com'})
    assert resp.status_code == 429
    mock_send.assert_not_called()


def test_otp_request_cooldown_429(client):
    just_now = {'created_at': datetime.now(timezone.utc)}
    with patch('models.count_recent_otps_by_ip', return_value=0), \
         patch('models.count_recent_otps', return_value=1), \
         patch('models.get_active_otp_by_identifier', return_value=just_now), \
         patch('services.otp_service.send_otp_email') as mock_send:
        resp = client.post('/api/auth/otp/request', json={'email': 'r@example.com'})
    assert resp.status_code == 429
    mock_send.assert_not_called()


def test_otp_request_email_send_failure_502(client):
    with patch('models.count_recent_otps_by_ip', return_value=0), \
         patch('models.count_recent_otps', return_value=0), \
         patch('models.get_active_otp_by_identifier', return_value=None), \
         patch('models.invalidate_active_otps', return_value=0), \
         patch('models.create_otp', return_value=1), \
         patch('services.otp_service.send_otp_email', return_value=False):
        resp = client.post('/api/auth/otp/request', json={'email': 'r@example.com'})
    assert resp.status_code == 502


# ── POST /api/auth/otp/verify (code path) ─────────────────────────────────

def _otp_row(code='123456', **over):
    row = {'id': 1, 'identifier': 'rider@example.com',
           'code_hash': otp_service.hash_code(code), 'attempts': 0}
    row.update(over)
    return row


def test_otp_verify_missing_fields_400(client):
    assert client.post('/api/auth/otp/verify', json={'email': 'r@example.com'}).status_code == 400
    assert client.post('/api/auth/otp/verify', json={'code': '123456'}).status_code == 400


def test_otp_verify_invalid_phone_400_before_consume(client):
    """A garbage/overlong phone is rejected with 400 BEFORE the code is looked up
    or consumed, so a bad phone can't burn the single-use code (or 500)."""
    with patch('models.get_active_otp_by_identifier') as mock_lookup, \
         patch('models.consume_otp') as mock_consume:
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'r@example.com', 'code': '123456',
                                 'phone': 'x' * 40})
    assert resp.status_code == 400
    mock_lookup.assert_not_called()
    mock_consume.assert_not_called()


def test_otp_request_rejects_overlong_email_400(client):
    resp = client.post('/api/auth/otp/request',
                       json={'email': 'a' * 250 + '@example.com'})   # > 255 chars
    assert resp.status_code == 400


def test_otp_verify_no_active_code_401(client):
    with patch('models.get_active_otp_by_identifier', return_value=None):
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'r@example.com', 'code': '123456'})
    assert resp.status_code == 401


def test_otp_verify_wrong_code_increments_and_401(client):
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row()), \
         patch('models.increment_otp_attempts', return_value=1) as mock_inc, \
         patch('models.consume_otp') as mock_consume:
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'rider@example.com', 'code': '999999'})
    assert resp.status_code == 401
    mock_inc.assert_called_once_with(1)
    mock_consume.assert_not_called()                          # wrong code never consumes


def test_otp_verify_too_many_attempts_429(client):
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row(attempts=otp_service.MAX_ATTEMPTS)), \
         patch('models.consume_otp') as mock_consume:
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'rider@example.com', 'code': '123456'})
    assert resp.status_code == 429
    mock_consume.assert_not_called()


def test_otp_verify_correct_code_existing_user_mints_token(client, app):
    user = {'id': 5, 'email': 'rider@example.com', 'profile_completed': True, 'rider_id': 7}
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row()), \
         patch('models.consume_otp', return_value=True) as mock_consume, \
         patch('models.get_user_by_email', return_value=user), \
         patch('models.update_user_login_time') as mock_touch, \
         patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'Rider@Example.com', 'code': '123456'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] == 7 and data['profile_complete'] is True
    mock_consume.assert_called_once_with(1)
    mock_touch.assert_called_once_with(5)
    with app.app_context():
        assert auth_mod.load_mobile_token(data['token']) == {'user_id': 5, 'rider_id': 7}


def test_otp_verify_existing_google_user_logs_in_via_email_code(client, app):
    """The headline goal: a Google-only account (no password) signs in with an
    email code that resolves to their SAME row — no new account created."""
    google_user = {'id': 3, 'email': 'rider@example.com', 'google_id': 'g-1',
                   'password_hash': None, 'profile_completed': True, 'rider_id': 6}
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row()), \
         patch('models.consume_otp', return_value=True), \
         patch('models.get_user_by_email', return_value=google_user), \
         patch('models.update_user_login_time'), \
         patch('models.get_user_by_id', return_value=google_user), \
         patch('models.create_user_email_otp') as mock_create:
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'rider@example.com', 'code': '123456'})
    assert resp.status_code == 200
    assert resp.get_json()['rider_id'] == 6
    mock_create.assert_not_called()                           # reused, not recreated


def test_otp_verify_new_user_created_with_phone(client):
    new_user = {'id': 9, 'email': 'new@example.com', 'phone': '+15551234567',
                'profile_completed': False, 'rider_id': None}
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row(identifier='new@example.com')), \
         patch('models.consume_otp', return_value=True), \
         patch('models.get_user_by_email', return_value=None), \
         patch('models.create_user_email_otp', return_value=new_user) as mock_create:
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'new@example.com', 'code': '123456',
                                 'phone': '+15551234567'})
    assert resp.status_code == 200
    assert resp.get_json()['profile_complete'] is False
    mock_create.assert_called_once_with('new@example.com', '+15551234567')


def test_otp_verify_existing_user_stores_new_phone(client):
    user = {'id': 5, 'email': 'rider@example.com', 'profile_completed': True, 'rider_id': 7}
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row()), \
         patch('models.consume_otp', return_value=True), \
         patch('models.get_user_by_email', return_value=user), \
         patch('models.update_user_login_time'), \
         patch('models.set_user_phone') as mock_phone, \
         patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'rider@example.com', 'code': '123456',
                                 'phone': '+15559998888'})
    assert resp.status_code == 200
    mock_phone.assert_called_once_with(5, '+15559998888')


def test_otp_verify_double_redeem_loses_race_401(client):
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row()), \
         patch('models.consume_otp', return_value=False), \
         patch('models.get_user_by_email') as mock_user:
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'rider@example.com', 'code': '123456'})
    assert resp.status_code == 401                            # already consumed
    mock_user.assert_not_called()


def test_otp_verify_signup_race_unique_violation_recovers(client, app):
    import psycopg2
    existing = {'id': 12, 'email': 'race@example.com', 'profile_completed': False, 'rider_id': None}
    with patch('models.get_active_otp_by_identifier', return_value=_otp_row(identifier='race@example.com')), \
         patch('models.consume_otp', return_value=True), \
         patch('models.get_user_by_email', side_effect=[None, existing]), \
         patch('models.create_user_email_otp',
               side_effect=psycopg2.errors.UniqueViolation('dup')):
        resp = client.post('/api/auth/otp/verify',
                           json={'email': 'race@example.com', 'code': '123456'})
    assert resp.status_code == 200                            # recovered by fetching the winner
    with app.app_context():
        assert auth_mod.load_mobile_token(resp.get_json()['token'])['user_id'] == 12


# ── POST /api/auth/otp/verify (magic-link path) ───────────────────────────

def test_otp_verify_link_token_existing_user(client, app):
    user = {'id': 5, 'email': 'rider@example.com', 'profile_completed': True, 'rider_id': 7}
    otp = {'id': 2, 'identifier': 'rider@example.com'}
    with patch('models.get_active_otp_by_link_hash', return_value=otp) as mock_lookup, \
         patch('models.consume_otp', return_value=True), \
         patch('models.get_user_by_email', return_value=user), \
         patch('models.update_user_login_time'), \
         patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/otp/verify', json={'link_token': 'magic-abc'})
    assert resp.status_code == 200
    # Looked up by the token's sha256, not the raw token.
    assert mock_lookup.call_args[0][0] == otp_service.hash_link_token('magic-abc')
    with app.app_context():
        assert auth_mod.load_mobile_token(resp.get_json()['token']) == {'user_id': 5, 'rider_id': 7}


def test_otp_verify_bad_link_token_401(client):
    with patch('models.get_active_otp_by_link_hash', return_value=None):
        resp = client.post('/api/auth/otp/verify', json={'link_token': 'nope'})
    assert resp.status_code == 401


# ── GET /api/auth/otp/magic (interstitial) ────────────────────────────────

def test_otp_magic_missing_token_410(client):
    resp = client.get('/api/auth/otp/magic')
    assert resp.status_code == 410


def test_otp_magic_invalid_token_410(client):
    with patch('models.get_active_otp_by_link_hash', return_value=None):
        resp = client.get('/api/auth/otp/magic?token=dead')
    assert resp.status_code == 410


def test_otp_magic_valid_token_redirects_into_app(client):
    with patch('models.get_active_otp_by_link_hash', return_value={'id': 2, 'identifier': 'r@example.com'}):
        resp = client.get('/api/auth/otp/magic?token=live-token')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'teamasha://auth/otp?token=live-token' in body      # deep link into the app
    assert 'window.location.replace' in body                   # auto-open


def test_otp_magic_escapes_hostile_token(client):
    """Defense-in-depth: even if a token somehow carried HTML/JS metacharacters,
    the interstitial must not emit a live <script>. (Real tokens are urlsafe and
    only echoed after a DB match, so this can't actually happen — the test locks
    the invariant.)"""
    with patch('models.get_active_otp_by_link_hash', return_value={'id': 2, 'identifier': 'r@example.com'}):
        resp = client.get('/api/auth/otp/magic?token=</script><script>alert(1)</script>')
    body = resp.get_data(as_text=True)
    # No raw injected script tag survives in either the href or the JS context.
    assert '<script>alert(1)' not in body
    assert '</script><script>' not in body


# ── models: query guards + counters (no DB; SQL is inspected / short-circuited) ──

import models


def test_active_otp_queries_filter_expiry_and_consumed():
    """Expiry + single-use live entirely in the SQL, so lock the guards against a
    silent regression (e.g. someone dropping the WHERE clause)."""
    captured = {}

    class _Cur:
        def fetchone(self):
            return None

    def fake_execute(sql, params=None):
        captured['sql'] = sql
        return _Cur()

    with patch('models._execute', side_effect=fake_execute):
        models.get_active_otp_by_identifier('r@example.com')
    assert 'consumed_at IS NULL' in captured['sql']
    assert 'expires_at > CURRENT_TIMESTAMP' in captured['sql']

    with patch('models._execute', side_effect=fake_execute):
        models.get_active_otp_by_link_hash('abc')
    assert 'consumed_at IS NULL' in captured['sql']
    assert 'expires_at > CURRENT_TIMESTAMP' in captured['sql']


def test_count_recent_otps_by_ip_none_is_zero_without_query():
    """A missing IP must never block a legit login — return 0 without touching DB."""
    with patch('models._execute') as mock_exec:
        assert models.count_recent_otps_by_ip(None, datetime.now(timezone.utc)) == 0
    mock_exec.assert_not_called()
