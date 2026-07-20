"""BrevetHub community surfaces — club-scoped directory / leaderboard / season
roster / public rider profile.

Everything is mocked: models are monkeypatched (no real DB), no HTTP is made, and
the fake model helpers are club-aware exactly like the real SQL (parameterized by
the viewer's club_id) so tenant isolation is proven structurally, not asserted
after the fact. Career numbers are date-independent (career km / brevet count /
SR-season list) so these tests never depend on the wall clock; the Nov 1 boundary
itself is proven in test_seasons.py.
"""
import contextlib
from datetime import date, datetime, timezone
from unittest.mock import patch

from shared import seasons

_UTC = timezone.utc

# --- Two clubs, several riders. Club A: alice/bob/carol. Club B: mallory.
#     dave is club-less. Emails + google ids are deliberately distinct strings so
#     a privacy leak into a rendered page is unmissable. --------------------- #
_ALICE_CACHE = [  # a Super Randonneur season (2024-2025): 200+300+400+600 = 1500
    {'date': '2024-11-15', 'distance_km': 200, 'finish_time': '13:30', 'route_name': 'Fall 200'},
    {'date': '2025-03-01', 'distance_km': 300, 'finish_time': '19:45', 'route_name': 'Spring 300'},
    {'date': '2025-04-01', 'distance_km': 400, 'finish_time': '26:00', 'route_name': 'Spring 400'},
    {'date': '2025-06-01', 'distance_km': 600, 'finish_time': '38:00', 'route_name': 'Summer 600'},
]
_BOB_CACHE = [   # a single 600 in the 2022-2023 season → 600 km, no SR, not 24-25
    {'date': '2022-12-10', 'distance_km': 600, 'finish_time': '39:00', 'route_name': 'Winter 600'},
]
_CAROL_CACHE = [  # a single 600 in 2023-2024 → 600 km, ties bob on career km
    {'date': '2023-12-10', 'distance_km': 600, 'finish_time': '39:30', 'route_name': 'Winter 600'},
]
_MALLORY_CACHE = [
    {'date': '2024-11-20', 'distance_km': 1000, 'finish_time': '70:00', 'route_name': 'Club B 1000'},
]
_DAVE_CACHE = [
    {'date': '2024-11-20', 'distance_km': 200, 'finish_time': '13:00', 'route_name': 'Solo 200'},
]

_MADE = datetime(2024, 3, 1, tzinfo=_UTC)
_ALICE = {'id': 1, 'email': 'alice@ex.com', 'google_id': 'g-alice-secret', 'rusa_id': '100',
          'club_id': 1, 'profile_completed': True, 'created_at': _MADE, 'rusa_cache': _ALICE_CACHE}
_BOB = {'id': 2, 'email': 'bob@ex.com', 'google_id': 'g-bob-secret', 'rusa_id': '200',
        'club_id': 1, 'profile_completed': True, 'created_at': _MADE, 'rusa_cache': _BOB_CACHE}
_CAROL = {'id': 3, 'email': 'carol@ex.com', 'google_id': 'g-carol-secret', 'rusa_id': '300',
          'club_id': 1, 'profile_completed': True, 'created_at': _MADE, 'rusa_cache': _CAROL_CACHE}
_MALLORY = {'id': 4, 'email': 'mallory@ex.com', 'google_id': 'g-mallory-secret', 'rusa_id': '900',
            'club_id': 2, 'profile_completed': True, 'created_at': _MADE, 'rusa_cache': _MALLORY_CACHE}
_DAVE = {'id': 5, 'email': 'dave@ex.com', 'google_id': 'g-dave-secret', 'rusa_id': '500',
         'club_id': None, 'profile_completed': True, 'created_at': _MADE, 'rusa_cache': _DAVE_CACHE}

_ALL = [_ALICE, _BOB, _CAROL, _MALLORY, _DAVE]
_BY_ID = {r['id']: r for r in _ALL}
_CLUBS = {1: {'id': 1, 'name': 'Club Alpha', 'city': 'SF', 'state': 'CA'},
          2: {'id': 2, 'name': 'Club Bravo', 'city': 'LA', 'state': 'CA'}}


# --- Club-aware fakes: mirror the real SQL, which is parameterized by club_id. -- #
def _fake_get_rider_by_id(rider_id, **kwargs):
    return _BY_ID.get(rider_id)


def _fake_get_club(club_id, **kwargs):
    return _CLUBS.get(club_id)


def _fake_club_riders(club_id, **kwargs):
    return [r for r in _ALL if r['club_id'] == club_id and r['profile_completed']]


def _fake_club_rider_by_rusa(club_id, rusa_id, **kwargs):
    for r in _ALL:
        if (r['club_id'] == club_id and r['profile_completed']
                and str(r['rusa_id']) == str(rusa_id)):
            return r
    return None


def _fake_rusa_cache(rider_id, **kwargs):
    r = _BY_ID.get(rider_id)
    return {'rusa_cache': r['rusa_cache'] if r else None,
            'rusa_fetched_at': datetime.now(_UTC)}


@contextlib.contextmanager
def _mocked():
    with patch('brevethub.models.get_rider_by_id', side_effect=_fake_get_rider_by_id), \
         patch('brevethub.models.get_club', side_effect=_fake_get_club), \
         patch('brevethub.models.get_club_riders_with_rusa', side_effect=_fake_club_riders), \
         patch('brevethub.models.get_club_rider_by_rusa', side_effect=_fake_club_rider_by_rusa), \
         patch('brevethub.models.get_rider_rusa_cache', side_effect=_fake_rusa_cache):
        yield


def _login(client, rider_id):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _get(client, rider_id, path):
    _login(client, rider_id)
    with _mocked():
        return client.get(path)


# --------------------------------------------------------------------------- #
# Directory
# --------------------------------------------------------------------------- #
def test_directory_lists_own_club_only(client):
    """A club-A viewer sees alice/bob/carol and never the club-B rider mallory."""
    resp = _get(client, _BOB['id'], '/riders')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'alice' in body and 'bob' in body and 'carol' in body
    assert 'mallory' not in body            # cross-club isolation (directory)


def test_directory_search_by_display_name(client):
    """Search narrows the club list to display-name matches."""
    resp = _get(client, _BOB['id'], '/riders?q=ali')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'alice' in body
    assert 'carol' not in body and 'bob' not in body.split('Search riders')[-1]


def test_directory_never_leaks_email_or_google_id(client):
    """A directory rendered for bob never contains another rider's full email or
    google_id — only the local-part display name."""
    resp = _get(client, _BOB['id'], '/riders')
    body = resp.get_data(as_text=True)
    for r in (_ALICE, _CAROL):
        assert r['email'] not in body          # full 'alice@ex.com' must not appear
        assert r['google_id'] not in body
    assert 'alice' in body                      # the display local-part does appear


def test_directory_club_less_viewer_gets_join_state(client):
    """A club-less viewer gets a graceful join-a-club state — no crash, no other
    club's riders leaked."""
    resp = _get(client, _DAVE['id'], '/riders')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Choose a club' in body
    assert 'alice' not in body and 'mallory' not in body


# --------------------------------------------------------------------------- #
# Public rider profile — access gate + privacy + career reuse
# --------------------------------------------------------------------------- #
def test_profile_same_club_returns_200(client):
    """A same-club viewer sees the target's public profile."""
    resp = _get(client, _BOB['id'], '/riders/100')       # bob views alice
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'alice' in body
    assert 'RUSA# 100' in body


def test_profile_cross_club_404(client):
    """A club-A viewer cannot open a club-B rider's profile → 404."""
    resp = _get(client, _BOB['id'], '/riders/900')       # bob (A) views mallory (B)
    assert resp.status_code == 404


def test_profile_cross_club_404_other_direction(client):
    """The club-B rider likewise cannot open a club-A rider's profile → 404."""
    resp = _get(client, _MALLORY['id'], '/riders/100')   # mallory (B) views alice (A)
    assert resp.status_code == 404


def test_profile_anonymous_redirects_to_login(client):
    """An anonymous request is bounced to login, never served the profile."""
    with _mocked():
        resp = client.get('/riders/100')
    assert resp.status_code in (301, 302)
    loc = resp.headers['Location']
    assert '/auth/login' in loc or '/login' in loc


def test_profile_self_view_works_even_club_less(client):
    """A rider can always view their own record, even before joining a club."""
    resp = _get(client, _DAVE['id'], '/riders/500')      # dave (no club) views self
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'dave' in body
    assert 'you' in body                                  # self badge


def test_profile_club_less_viewer_cannot_view_others(client):
    """A club-less viewer cannot view another rider's profile → 404."""
    resp = _get(client, _DAVE['id'], '/riders/100')      # dave views alice
    assert resp.status_code == 404


def test_profile_never_leaks_email_or_google_id(client):
    """Another rider's profile view exposes the display name only — never the full
    email address or google_id."""
    resp = _get(client, _BOB['id'], '/riders/100')       # bob views alice
    body = resp.get_data(as_text=True)
    assert _ALICE['email'] not in body
    assert _ALICE['google_id'] not in body
    assert 'alice' in body


def test_profile_career_numbers_reuse_shared_engine(client):
    """The profile's career numbers equal shared.seasons.career_summary over the
    target's cached RUSA history — no reimplementation."""
    expected = seasons.career_summary(_ALICE_CACHE, date.today())
    resp = _get(client, _BOB['id'], '/riders/100')
    body = resp.get_data(as_text=True)
    assert str(expected['total_km']) in body              # 1500 km
    assert '{} brevets'.format(expected['count']) in body  # (4 brevets)
    assert '2024-2025' in body                            # the SR season
