"""BrevetHub brevet calendar — rendering, cache behavior, and failure fallback.

Follows the established BrevetHub test pattern: monkeypatch `brevethub.models.*`
and the calendar route's imported `get_rusa_events`, use the `client` fixture,
never touch a real DB or network. The security/honesty contracts are first-class:
  - the guest calendar exposes NO rider PII,
  - start location/time render only when present, else an honest "—" placeholder
    (asserted on the rendered HTML — never a fabricated value),
  - cache hit skips the scrape; cache miss scrapes+upserts; a scrape failure serves
    the stale cache with a banner and NEVER 500s, and an empty scrape never wipes it.
"""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

# One upcoming national-feed event: no start location/time (as the feed provides).
_EVENT_NO_START = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes Lighthouse 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200, 'rwgps_url': None,
    'start_location': None, 'start_time': None, 'time_limit_hours': 13.5,
}
# An event a richer source DID supply a start for (proves the "when present" path).
_EVENT_WITH_START = dict(_EVENT_NO_START, id=12, name='Ferry Building 300',
                         distance_km=300, start_location='SF Ferry Building',
                         start_time='06:00')


def _now():
    return datetime.now(timezone.utc)


def _stale():
    return datetime(2000, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Guest browse — renders events, honest placeholder, no PII
# --------------------------------------------------------------------------- #
def test_guest_calendar_renders_event_with_placeholder(client):
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events') as mock_scrape, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Cache is fresh → no scrape.
    mock_scrape.assert_not_called()
    # Event fields render.
    assert 'Point Reyes Lighthouse 200' in body
    assert '200 km' in body
    assert 'CA: San Francisco' in body
    # No start location/time from the national feed → honest placeholder, no value.
    assert '—' in body
    # Guest sees no rider identity and no sign-up controls.
    assert 'rider@example.com' not in body
    assert 'signup-btn' not in body


def test_guest_calendar_renders_start_when_present(client):
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_WITH_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'SF Ferry Building' in body
    assert '06:00' in body


def test_empty_calendar_shows_no_events_message(client):
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    assert 'No upcoming brevets' in resp.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Cache hit vs miss vs failure
# --------------------------------------------------------------------------- #
def test_cache_hit_does_not_scrape(client):
    """A fresh cache serves rows without any HTTP scrape."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events') as mock_scrape, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    mock_scrape.assert_not_called()


def test_cache_miss_scrapes_and_upserts(client):
    """An empty/stale cache triggers a scrape and upserts each event."""
    scraped = [dict(_EVENT_NO_START, route_id='1234')]
    with patch('brevethub.models.get_events_cache_freshness', return_value=None), \
         patch('brevethub.routes.calendar.get_rusa_events', return_value=scraped) as mock_scrape, \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    mock_scrape.assert_called_once()
    mock_upsert.assert_called_once()


def test_scrape_failure_serves_stale_cache_no_500(client):
    """A scrape exception with a prior cache → 200 + soft banner, never 500, and the
    cached rows still render."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_stale()), \
         patch('brevethub.routes.calendar.get_rusa_events', side_effect=OSError('rusa down')), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'cached calendar' in body            # soft degradation banner
    assert 'Point Reyes Lighthouse 200' in body  # stale cache still served
    mock_upsert.assert_not_called()              # empty/failed scrape never overwrites


def test_scrape_failure_no_cache_shows_empty_banner_no_500(client):
    """A scrape failure with NO prior cache → 200 with an 'unavailable' banner."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=None), \
         patch('brevethub.routes.calendar.get_rusa_events', side_effect=OSError('rusa down')), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    assert 'temporarily unavailable' in resp.get_data(as_text=True)
    mock_upsert.assert_not_called()


def test_empty_scrape_does_not_overwrite_cache(client):
    """A scrape that returns [] must NOT upsert (never clobber a good cache with an
    empty result)."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_stale()), \
         patch('brevethub.routes.calendar.get_rusa_events', return_value=[]), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    mock_upsert.assert_not_called()


# --------------------------------------------------------------------------- #
# Signed-in rider — own status + optional region scope
# --------------------------------------------------------------------------- #
def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def test_rider_sees_own_status_and_signup_controls(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value={'id': 3, 'name': 'SFR', 'state': 'CA'}), \
         patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]), \
         patch('brevethub.models.get_rider_signup_statuses',
               return_value=[{'event_id': 11, 'status': 'going'}]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'signup-btn' in body            # rider gets the sign-up controls
    assert 'Going' in body                 # their own current status shows


def test_rider_region_scope_filters_by_club_state(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value={'id': 3, 'name': 'SFR', 'state': 'CA'}), \
         patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]) as mock_upcoming, \
         patch('brevethub.models.get_rider_signup_statuses', return_value=[]):
        resp = client.get('/calendar?scope=club')
    assert resp.status_code == 200
    # The rider's club state is passed as the region filter.
    assert mock_upcoming.call_args.kwargs.get('state') == 'CA'
