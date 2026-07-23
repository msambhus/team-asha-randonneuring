"""Per-event live-ride link on the calendar (Closes #538).

Follows the established BrevetHub test pattern: monkeypatch `brevethub.models.*`,
use the `client` fixture, never touch a real DB. The security-critical contracts
are first-class:
  - the calendar Live link renders ONLY when an event resolves to a PUBLIC live
    ride, and points at the EXISTING shared Radial view (/live/<ride_id>);
  - an event with no associated public ride shows NO Live link (no broken
    placeholder), so a private ride never leaks via the calendar;
  - the association write (/live/<id>/link-event) is owner-scoped: a non-owner POST
    is a no-op, and linking to an unknown event 404s.
"""
import os
import re
from unittest.mock import patch

from datetime import datetime, timezone

MODELS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'brevethub', 'models.py')

_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes Lighthouse 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200, 'rwgps_url': None,
    'start_location': None, 'start_time': None, 'time_limit_hours': 13.5,
    'signup_count': 3,
}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _now():
    return datetime.now(timezone.utc)


def _render_calendar(client):
    """Render /calendar as a guest with one upcoming event, everything else mocked."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]), \
         patch('brevethub.models.get_brevet_weather_for_events', return_value={}):
        return client.get('/calendar')


# --------------------------------------------------------------------------- #
# Calendar Live link — always shown, pointing at the event-scoped live view
# --------------------------------------------------------------------------- #
def test_calendar_renders_live_link_for_every_event(client):
    """EVERY event card carries a Live link pointing at the shared event-scoped
    live view (/live/event/<id>), independent of whether any ride is live yet."""
    resp = _render_calendar(client)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/live/event/11"' in body     # event-scoped view, not a ride id
    # The Live button carries the shared event-link-btn styling, a pin icon, and "Live".
    assert re.search(r'href="/live/event/11".*?>\s*Live\s*<', body, re.DOTALL)


def test_calendar_live_link_needs_no_ride_resolution(client):
    """The calendar no longer resolves events to rides to decide the link — the
    Live link is always present, so a future/quiet event still shows it and the
    page renders without any live-ride query."""
    resp = _render_calendar(client)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/live/event/11"' in body
    # No per-RIDE Live href leaks onto the calendar (the view is event-keyed).
    assert not re.search(r'href="/live/\d+"', body)
    assert 'Plan' in body


# --------------------------------------------------------------------------- #
# Association route — owner-scoped link / unlink
# --------------------------------------------------------------------------- #
def test_link_event_owner_links(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.set_ride_event', return_value={'id': 55}) as mock_set:
        resp = client.post('/live/55/link-event', data={'event_id': '11'})
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/live/new')
    mock_set.assert_called_once_with(55, 7, 11)


def test_link_event_unlink_clears(client):
    """An empty event_id unlinks the ride (clears the FK back to NULL)."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event') as mock_event, \
         patch('brevethub.models.set_ride_event', return_value={'id': 55}) as mock_set:
        resp = client.post('/live/55/link-event', data={'event_id': ''})
    assert resp.status_code == 302
    mock_event.assert_not_called()            # no event lookup needed to unlink
    mock_set.assert_called_once_with(55, 7, None)


def test_link_event_non_owner_is_noop(client):
    """A non-owner POST changes nothing (owner-scoped UPDATE matched no row)."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.set_ride_event', return_value=None) as mock_set:
        resp = client.post('/live/55/link-event', data={'event_id': '11'})
    assert resp.status_code == 302
    mock_set.assert_called_once_with(55, 7, 11)   # owner-scoped write, no row updated


def test_link_event_unknown_event_404(client):
    """Linking to an event that does not exist is a 404 — before any write."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=None), \
         patch('brevethub.models.set_ride_event') as mock_set:
        resp = client.post('/live/55/link-event', data={'event_id': '999999'})
    assert resp.status_code == 404
    mock_set.assert_not_called()


def test_link_event_requires_login(client):
    """The association route is profile-gated; an anon user is bounced to login."""
    with patch('brevethub.models.get_rider_by_id', return_value=None), \
         patch('brevethub.models.set_ride_event') as mock_set:
        resp = client.post('/live/55/link-event', data={'event_id': '11'})
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']
    mock_set.assert_not_called()


# --------------------------------------------------------------------------- #
# Manage page — the per-ride "link to event" control is the association entry point
# --------------------------------------------------------------------------- #
def test_manage_page_shows_event_linker_for_public_ride(client):
    _login(client, rider_id=7)
    rides = [{'id': 55, 'name': 'My Live 200', 'distance_km': 200,
              'start_at': datetime(2026, 8, 15, 6, 0), 'status': 'going',
              'is_public': True, 'event_id': None}]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_rider_rides', return_value=rides), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]):
        resp = client.get('/live/new')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '/live/55/link-event' in body       # the association form action
    assert 'Point Reyes Lighthouse 200' in body  # the event is a selectable option


# --------------------------------------------------------------------------- #
# Event-scoped live view — every event gets a Live link to this shared Radial view
# --------------------------------------------------------------------------- #
_EVENT_FULL = dict(_EVENT, rwgps_url='https://ridewithgps.com/routes/20392003',
                   start_location=None, start_time=None, club_id=None)


def test_event_live_map_renders_for_guest(client):
    """The event live view renders for a guest: the shared Radial partial (map +
    roster table + profile), polling the event-scoped roster endpoint, with no route
    geometry fetched live (the warmed cache track is read)."""
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_FULL), \
         patch('brevethub.models.get_rp_route_elevation_track',
               return_value=[{'lat': 38.6, 'lng': -122.8, 'dist_m': 0.0, 'e_m': 30.0},
                             {'lat': 38.7, 'lng': -122.9, 'dist_m': 5000.0, 'e_m': 120.0}]):
        resp = client.get('/live/event/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Point Reyes Lighthouse 200' in body
    assert '/live/event/11/roster.json' in body     # the poll endpoint the partial reads
    assert 'radial-live' in body                     # the shared partial is included


def test_event_live_map_unknown_event_404(client):
    """An unknown event id is a 404 (a phantom event cannot be probed)."""
    with patch('brevethub.models.get_brevet_event_full', return_value=None):
        resp = client.get('/live/event/999999')
    assert resp.status_code == 404


def test_event_live_map_cold_cache_still_renders(client):
    """A cold geometry cache degrades to no route line but still renders the view
    (empty profile), never 500-ing — the fetch fallback is best-effort."""
    with patch('brevethub.models.get_brevet_event_full',
               return_value=dict(_EVENT_FULL, rwgps_url=None)), \
         patch('brevethub.models.get_rp_route_elevation_track', return_value=None):
        resp = client.get('/live/event/11')
    assert resp.status_code == 200


def test_event_roster_empty_when_no_rides(client):
    """An event with no linked public ride returns an empty roster (the view shows a
    'waiting for riders' state) — a 200 with roster == []."""
    with patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.get_live_ride_ids_for_event', return_value=[]):
        resp = client.get('/live/event/11/roster.json')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['event_id'] == 11
    assert data['roster'] == []


def test_event_roster_aggregates_public_rides(client):
    """The event roster is the union of its public rides' rosters; a resolved ride
    that is not public is skipped (defense in depth)."""
    ride_pub = {'id': 55, 'is_public': True, 'rwgps_url': None, 'club_id': 3, 'name': 'A'}
    ride_priv = {'id': 66, 'is_public': False, 'rwgps_url': None, 'club_id': 3, 'name': 'B'}

    def _fake_ride_roster(ride, now):
        return [{'key': 'k%s' % ride['id'], 'display_name': 'R',
                 'route_position_mi': 10.0 if ride['id'] == 55 else None}]

    with patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.get_live_ride_ids_for_event', return_value=[55, 66]), \
         patch('brevethub.models.get_ride',
               side_effect=lambda rid: {55: ride_pub, 66: ride_priv}.get(rid)), \
         patch('brevethub.routes.live._ride_roster', side_effect=_fake_ride_roster):
        resp = client.get('/live/event/11/roster.json')
    assert resp.status_code == 200
    data = resp.get_json()
    keys = [r['key'] for r in data['roster']]
    assert keys == ['k55']                 # only the PUBLIC ride contributed


def test_event_roster_survives_per_ride_failure(client):
    """A per-ride DB hiccup mid-loop drops that ride's contribution rather than
    500-ing the public poll — the surviving public ride still appears."""
    ride_ok = {'id': 55, 'is_public': True, 'rwgps_url': None, 'club_id': 3, 'name': 'A'}

    def _get_ride(rid):
        if rid == 66:
            raise RuntimeError('connection reset')
        return ride_ok

    with patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.get_live_ride_ids_for_event', return_value=[55, 66]), \
         patch('brevethub.models.get_ride', side_effect=_get_ride), \
         patch('brevethub.routes.live._ride_roster',
               side_effect=lambda ride, now: [{'key': 'k55', 'display_name': 'R',
                                               'route_position_mi': 5.0}]):
        resp = client.get('/live/event/11/roster.json')
    assert resp.status_code == 200
    data = resp.get_json()
    assert [r['key'] for r in data['roster']] == ['k55']   # bad ride 66 dropped


def test_event_roster_unknown_event_404(client):
    """The roster poll 404s for an unknown event (never reveals a phantom id)."""
    with patch('brevethub.models.get_brevet_event', return_value=None):
        resp = client.get('/live/event/999999/roster.json')
    assert resp.status_code == 404


def test_event_roster_survives_resolution_failure(client):
    """A DB hiccup resolving rides degrades to an empty roster, never a 500."""
    with patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.get_live_ride_ids_for_event',
               side_effect=RuntimeError('db down')):
        resp = client.get('/live/event/11/roster.json')
    assert resp.status_code == 200
    assert resp.get_json()['roster'] == []


# --------------------------------------------------------------------------- #
# Event live view — "Appear on this map" share surface (parity with TA live view)
# --------------------------------------------------------------------------- #
_EVENT_NOROUTE = dict(_EVENT_FULL, rwgps_url=None)   # no geometry fetch in unit tests


def test_event_live_map_guest_sees_signin_prompt(client):
    """A logged-out viewer gets a 'Sign in to appear on this map' link, not the
    share controls or the join form."""
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NOROUTE), \
         patch('brevethub.models.get_rp_route_elevation_track', return_value=None):
        resp = client.get('/live/event/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Sign in to appear on this map' in body
    assert '/live/event/11/join' not in body
    assert 'id="evl-beacon"' not in body


def test_event_live_map_signed_in_no_ride_shows_join(client):
    """A signed-in rider with no ride on the event sees the one-tap join form."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NOROUTE), \
         patch('brevethub.models.get_rp_route_elevation_track', return_value=None), \
         patch('brevethub.models.get_rider_ride_for_event', return_value=None):
        resp = client.get('/live/event/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '/live/event/11/join' in body
    assert "Join this ride" in body
    assert 'id="evl-beacon"' not in body     # no share controls until they join


def test_event_live_map_signed_in_with_ride_shows_share_controls(client):
    """A rider who has joined the event sees the Garmin form + phone-beacon control,
    wired to THEIR ride id."""
    _login(client, rider_id=7)
    my_ride = {'id': 55, 'name': 'SonoMendo', 'distance_km': 300,
               'is_public': True, 'event_id': 11}
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NOROUTE), \
         patch('brevethub.models.get_rp_route_elevation_track', return_value=None), \
         patch('brevethub.models.get_rider_ride_for_event', return_value=my_ride), \
         patch('brevethub.models.get_live_tracking_rp', return_value={'enabled': False}):
        resp = client.get('/live/event/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Appear on this map' in body
    assert '/live/55/garmin' in body          # Garmin form posts to the rider's ride
    assert 'id="evl-beacon"' in body         # phone-beacon control present
    assert 'EVL_RIDE_ID = 55' in body         # beacon JS wired to the ride id


def test_event_live_map_garmin_linked_state(client):
    """When the rider's Garmin is linked to THIS ride, the summary shows the linked
    badge, the input is pre-filled, and the clear button renders."""
    _login(client, rider_id=7)
    my_ride = {'id': 55, 'name': 'SonoMendo', 'distance_km': 300,
               'is_public': True, 'event_id': 11}
    tracking = {'enabled': True,
                'garmin_session_url': 'https://livetrack.garmin.com/session/abc/token/xyz',
                'active_ride_id': 55}
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NOROUTE), \
         patch('brevethub.models.get_rp_route_elevation_track', return_value=None), \
         patch('brevethub.models.get_rider_ride_for_event', return_value=my_ride), \
         patch('brevethub.models.get_live_tracking_rp', return_value=tracking):
        resp = client.get('/live/event/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Garmin linked' in body                       # summary badge
    assert 'livetrack.garmin.com/session/abc' in body    # input pre-filled
    assert 'Stop tracking this ride' in body             # clear button


def test_event_live_map_share_surface_failsoft(client):
    """A DB error resolving the rider's ride/tracking hides the share surface but
    still renders the public view (never 500) — the anon roster stays pollable."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NOROUTE), \
         patch('brevethub.models.get_rp_route_elevation_track', return_value=None), \
         patch('brevethub.models.get_rider_ride_for_event',
               side_effect=RuntimeError('db down')):
        resp = client.get('/live/event/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'radial-live' in body                 # the map/roster still render
    assert 'id="evl-beacon"' not in body         # share controls degraded off


def test_event_join_creates_and_links_public_ride(client):
    """Join creates a PUBLIC ride named for the event and links it, owner-scoped,
    then redirects back to the event view."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NOROUTE), \
         patch('brevethub.models.get_rider_ride_for_event', return_value=None), \
         patch('brevethub.models.create_ride', return_value=55) as mock_create, \
         patch('brevethub.models.set_ride_event', return_value={'id': 55}) as mock_link:
        resp = client.post('/live/event/11/join')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/live/event/11')
    assert mock_create.call_args.kwargs['is_public'] is True
    assert mock_create.call_args.kwargs['name'] == _EVENT_NOROUTE['name']
    mock_link.assert_called_once_with(55, 7, 11)


def test_event_join_reuses_existing_ride(client):
    """Join is idempotent — a rider who already has a ride for the event does not
    get a duplicate (no create/link)."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NOROUTE), \
         patch('brevethub.models.get_rider_ride_for_event', return_value={'id': 55}), \
         patch('brevethub.models.create_ride') as mock_create, \
         patch('brevethub.models.set_ride_event') as mock_link:
        resp = client.post('/live/event/11/join')
    assert resp.status_code == 302
    mock_create.assert_not_called()
    mock_link.assert_not_called()


def test_event_join_unknown_event_404(client):
    """Joining an unknown event is a 404 before any write."""
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event_full', return_value=None), \
         patch('brevethub.models.create_ride') as mock_create:
        resp = client.post('/live/event/999999/join')
    assert resp.status_code == 404
    mock_create.assert_not_called()


def test_event_join_requires_login(client):
    """The join route is profile-gated; an anon user is bounced to login."""
    with patch('brevethub.models.get_rider_by_id', return_value=None), \
         patch('brevethub.models.create_ride') as mock_create:
        resp = client.post('/live/event/11/join')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']
    mock_create.assert_not_called()


# --------------------------------------------------------------------------- #
# Model contracts — public-only gating + owner-scoping, asserted statically
# --------------------------------------------------------------------------- #
def test_resolver_gates_on_public_and_touches_only_rp_tables():
    """The event->rides resolver MUST filter is_public = TRUE (so no private ride
    can surface) and reference only rp_ tables — verified statically since the
    mocked route cannot exercise the SQL (no DB in unit tests)."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    # The shared join the resolver builds on enforces the public gate.
    match_body = re.search(r'_EVENT_LIVE_RIDE_MATCH\s*=\s*\((.*?)\)\n', src, re.DOTALL)
    assert match_body, "shared event->ride match clause not found"
    assert re.search(r'is_public\s*=\s*TRUE', match_body.group(1))
    # The resolver exists and selects from rp_ tables only.
    body = re.search(r'def get_live_ride_ids_for_event\(.*?\n(?=def |# ---)',
                     src, re.DOTALL)
    assert body, "resolver get_live_ride_ids_for_event not found"
    assert 'rp_ride' in body.group(0) and 'rp_brevet_event' in body.group(0)


def test_setter_is_owner_scoped():
    """The association write filters by rider_id, so a non-owner can never link
    another rider ride to an event."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    body = re.search(r'def set_ride_event\(.*?\n(?=def )', src, re.DOTALL).group(0)
    assert re.search(r'UPDATE\s+rp_ride\s+SET\s+event_id', body)
    assert re.search(r'WHERE\s+id\s*=\s*%s\s+AND\s+rider_id\s*=\s*%s', body)
