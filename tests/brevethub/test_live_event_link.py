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


def _render_calendar(client, live_rides):
    """Render /calendar as a guest with one upcoming event and the given
    ``{event_id: ride_id}`` live-ride resolution, everything else mocked."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]), \
         patch('brevethub.models.get_brevet_weather_for_events', return_value={}), \
         patch('brevethub.models.get_live_ride_ids_for_events', return_value=live_rides):
        return client.get('/calendar')


# --------------------------------------------------------------------------- #
# Calendar Live link — renders only for an event that resolves to a public ride
# --------------------------------------------------------------------------- #
def test_calendar_renders_live_link_when_event_resolves(client):
    """An event that resolves to a public live ride shows a Live link pointing at
    the shared Radial view (/live/<ride_id>)."""
    resp = _render_calendar(client, {11: 55})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/live/55"' in body          # links to the existing Radial view
    # The Live button carries the shared event-link-btn styling and the "Live" label.
    assert re.search(r'href="/live/55"[^>]*>\s*Live\s*<', body)


def test_calendar_no_live_link_when_event_unresolved(client):
    """An event with no associated public ride shows NO Live link and no broken
    placeholder (the resolver simply omits it from the map)."""
    resp = _render_calendar(client, {})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # No per-ride Live button href for this event. The Plan link is still there.
    assert 'href="/live/55"' not in body
    assert not re.search(r'href="/live/\d+"', body)
    assert 'Plan' in body


def test_calendar_survives_resolution_failure(client):
    """A DB hiccup in resolution drops the Live links rather than 500-ing the page
    (fail-soft, mirroring the my_results guard)."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]), \
         patch('brevethub.models.get_brevet_weather_for_events', return_value={}), \
         patch('brevethub.models.get_live_ride_ids_for_events',
               side_effect=RuntimeError('db down')):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert not re.search(r'href="/live/\d+"', body)  # links dropped, page intact
    assert 'Point Reyes Lighthouse 200' in body


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
# Model contracts — public-only gating + owner-scoping, asserted statically
# --------------------------------------------------------------------------- #
def test_bulk_resolver_returns_empty_for_no_events():
    """The page-bulk resolver short-circuits an empty list with no DB query."""
    from brevethub import models
    assert models.get_live_ride_ids_for_events([]) == {}


def test_resolvers_gate_on_public_and_touch_only_rp_tables():
    """Both event->ride resolvers MUST filter is_public = TRUE (so no private ride
    can surface) and reference only rp_ tables — verified statically since the
    mocked route cannot exercise the SQL (no DB in unit tests)."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    # The shared join both resolvers build on enforces the public gate.
    match_body = re.search(r'_EVENT_LIVE_RIDE_MATCH\s*=\s*\((.*?)\)\n', src, re.DOTALL)
    assert match_body, "shared event->ride match clause not found"
    assert re.search(r'is_public\s*=\s*TRUE', match_body.group(1))
    # Both resolvers exist and select from rp_ tables only.
    for fn in ('get_live_ride_id_for_event', 'get_live_ride_ids_for_events'):
        body = re.search(r'def %s\(.*?\n(?=def |# ---)' % fn, src, re.DOTALL)
        assert body, "resolver %s not found" % fn
        assert 'rp_ride' in body.group(0) and 'rp_brevet_event' in body.group(0)


def test_setter_is_owner_scoped():
    """The association write filters by rider_id, so a non-owner can never link
    another rider ride to an event."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    body = re.search(r'def set_ride_event\(.*?\n(?=def )', src, re.DOTALL).group(0)
    assert re.search(r'UPDATE\s+rp_ride\s+SET\s+event_id', body)
    assert re.search(r'WHERE\s+id\s*=\s*%s\s+AND\s+rider_id\s*=\s*%s', body)
