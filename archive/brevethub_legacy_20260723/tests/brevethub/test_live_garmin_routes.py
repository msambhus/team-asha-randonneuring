"""Member live-tracking setup UI — settings toggle + self-scoped Garmin attach.

Follows the BrevetHub pattern: monkeypatch brevethub.models.*, use the `client`
fixture, never a real DB. The redteam blocker (attach authorization) is a
first-class matrix here:
  - registration is SELF-scoped — set/clear always receive the SESSION rider_id,
    never a ride-owner id,
  - a logged-in rider may attach to a PUBLIC ride they don't own (this is the
    multi-rider join) but a PRIVATE ride they don't own 404s,
  - an owner may attach to their own private ride,
  - anonymous is bounced to login before any lookup, a junk URL writes nothing.
"""
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

_GARMIN_URL = 'https://livetrack.garmin.com/session/SESS/token/TOK'


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Settings toggle (self-scoped)
# --------------------------------------------------------------------------- #
def test_settings_get_renders_toggle(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp',
               return_value={'rider_id': 7, 'enabled': True}):
        resp = client.get('/live/settings')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'Enable live tracking' in body
    assert 'name="enabled"' in body


def test_settings_post_enables_for_session_rider(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.upsert_rider_live_tracking_rp', return_value=True) as up:
        resp = client.post('/live/settings', data={'enabled': 'on'})
    assert resp.status_code == 302
    up.assert_called_once_with(7, True)     # subject = the session rider


def test_settings_post_disables(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.upsert_rider_live_tracking_rp', return_value=True) as up:
        resp = client.post('/live/settings', data={})    # unchecked box → off
    assert resp.status_code == 302
    up.assert_called_once_with(7, False)


def test_settings_requires_login(client):
    with patch('brevethub.models.get_rider_by_id', return_value=None):
        resp = client.get('/live/settings')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


# --------------------------------------------------------------------------- #
# Garmin attach — the authorization matrix
# --------------------------------------------------------------------------- #
def test_attach_anonymous_redirects_to_login_without_write(client):
    with patch('brevethub.models.get_rider_by_id', return_value=None), \
         patch('brevethub.models.get_ride') as get_ride, \
         patch('brevethub.models.set_ride_garmin_rp') as set_g:
        resp = client.post('/live/1/garmin', data={'garmin_session_url': _GARMIN_URL})
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']
    get_ride.assert_not_called()            # auth checked before any lookup
    set_g.assert_not_called()


def test_attach_non_owner_to_public_ride_writes_own_row(client):
    """The multi-rider join: any logged-in rider may attach to a PUBLIC ride."""
    _login(client, rider_id=7)
    public_ride = {'id': 1, 'rider_id': 99, 'is_public': True}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=public_ride), \
         patch('brevethub.models.set_ride_garmin_rp', return_value=True) as set_g:
        resp = client.post('/live/1/garmin', data={'garmin_session_url': _GARMIN_URL})
    assert resp.status_code == 302
    # Writes THE SESSION rider's own row (7) pointed at ride 1 — never owner 99.
    set_g.assert_called_once_with(7, 1, _GARMIN_URL, 'TOK')


def test_attach_non_owner_to_private_ride_404s_without_write(client):
    _login(client, rider_id=7)
    private_ride = {'id': 1, 'rider_id': 99, 'is_public': False}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=private_ride), \
         patch('brevethub.models.set_ride_garmin_rp') as set_g:
        resp = client.post('/live/1/garmin', data={'garmin_session_url': _GARMIN_URL})
    assert resp.status_code == 404
    set_g.assert_not_called()


def test_attach_owner_to_own_private_ride_writes_own_row(client):
    _login(client, rider_id=7)
    own_private = {'id': 1, 'rider_id': 7, 'is_public': False}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=own_private), \
         patch('brevethub.models.set_ride_garmin_rp', return_value=True) as set_g:
        resp = client.post('/live/1/garmin', data={'garmin_session_url': _GARMIN_URL})
    assert resp.status_code == 302
    set_g.assert_called_once_with(7, 1, _GARMIN_URL, 'TOK')


def test_attach_junk_url_writes_nothing(client):
    _login(client, rider_id=7)
    public_ride = {'id': 1, 'rider_id': 99, 'is_public': True}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=public_ride), \
         patch('brevethub.models.set_ride_garmin_rp') as set_g:
        resp = client.post('/live/1/garmin', data={'garmin_session_url': 'not-a-link'})
    assert resp.status_code == 302
    set_g.assert_not_called()


def test_attach_unknown_ride_404s(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=None), \
         patch('brevethub.models.set_ride_garmin_rp') as set_g:
        resp = client.post('/live/999/garmin', data={'garmin_session_url': _GARMIN_URL})
    assert resp.status_code == 404
    set_g.assert_not_called()


def test_clear_is_self_scoped_to_session_rider(client):
    _login(client, rider_id=7)
    public_ride = {'id': 1, 'rider_id': 99, 'is_public': True}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=public_ride), \
         patch('brevethub.models.clear_ride_garmin_rp', return_value=True) as clr:
        resp = client.post('/live/1/garmin', data={'action': 'clear'})
    assert resp.status_code == 302
    clr.assert_called_once_with(7, 1)       # only the session rider's own row
