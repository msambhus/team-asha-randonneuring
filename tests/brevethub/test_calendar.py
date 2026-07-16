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

import pytest


@pytest.fixture(autouse=True)
def _stub_weather_cache():
    """M7 added a weather-cache read (``models.get_brevet_weather_for_events``) to
    the ``/calendar`` route. These calendar tests predate weather and don't exercise
    it, so stub it to an empty cache — keeping them fully mocked / DB-free. Weather
    badge rendering is covered by test_calendar_weather.py."""
    with patch('brevethub.models.get_brevet_weather_for_events', return_value={}):
        yield


_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

# One upcoming national-feed event: no start location/time (as the feed provides).
_EVENT_NO_START = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes Lighthouse 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200, 'rwgps_url': None,
    'start_location': None, 'start_time': None, 'time_limit_hours': 13.5,
    'signup_count': 3,
}
# An event a richer source DID supply a start for (proves the "when present" path),
# plus a RideWithGPS route link (proves the route-map link renders when present).
_EVENT_WITH_START = dict(_EVENT_NO_START, id=12, name='Ferry Building 300',
                         distance_km=300, start_location='SF Ferry Building',
                         start_time='06:00', signup_count=0,
                         rwgps_url='https://ridewithgps.com/routes/99')


def _now():
    return datetime.now(timezone.utc)


def _stale():
    """A PRESENT-but-old cache timestamp (far past CALENDAR_STALE_AFTER).

    Under the seed-only policy a present cache — however old — is served WITHOUT a
    request-path scrape; this timestamp only trips the soft 'stale' display banner.
    """
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
    assert '4200 ft climbing' in body            # elevation renders when present
    # New month-grouped card structure (the styling/feature-parity classes).
    assert 'event-card' in body
    assert 'events-grid' in body
    assert 'month-header' in body
    assert 'August 2026' in body                 # month grouping label
    assert '3 interested' in body                # aggregate signup count renders
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
    # RideWithGPS route link renders when the event has one.
    assert 'https://ridewithgps.com/routes/99' in body
    assert 'Route map' in body


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


def test_empty_cache_seed_scrapes_and_upserts(client):
    """A truly EMPTY cache (None freshness) triggers the one-time seed scrape and
    upserts each event — the ONLY on-request path that scrapes (first-deploy seed)."""
    scraped = [dict(_EVENT_NO_START, route_id='1234')]
    with patch('brevethub.models.get_events_cache_freshness', return_value=None), \
         patch('brevethub.routes.calendar.get_rusa_events', return_value=scraped) as mock_scrape, \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    mock_scrape.assert_called_once()
    mock_upsert.assert_called_once()


def test_stale_present_cache_is_served_without_scrape(client):
    """REDTEAM FIX (pinned): a PRESENT-but-stale cache must serve the cached rows and
    show the soft 'stale' banner WITHOUT any request-path scrape. The heavy scrape is
    the cron's job — a present cache is never re-scraped synchronously on load."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_stale()), \
         patch('brevethub.routes.calendar.get_rusa_events') as mock_scrape, \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'cached calendar' in body             # soft 'stale' degradation banner
    assert 'Point Reyes Lighthouse 200' in body  # cached rows still served
    mock_scrape.assert_not_called()              # NO synchronous scrape on a present cache
    mock_upsert.assert_not_called()


def test_present_recent_cache_no_banner_no_scrape(client):
    """A present, recent cache (within CALENDAR_STALE_AFTER) → no banner AND no scrape."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=_now()), \
         patch('brevethub.routes.calendar.get_rusa_events') as mock_scrape, \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT_NO_START]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'cached calendar' not in body         # fresh → no stale banner
    assert 'temporarily unavailable' not in body
    mock_scrape.assert_not_called()


def test_seed_failure_no_cache_shows_empty_banner_no_500(client):
    """A seed scrape failure with NO prior cache → 200 with an 'unavailable' banner,
    never a 500, and no upsert."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=None), \
         patch('brevethub.routes.calendar.get_rusa_events', side_effect=OSError('rusa down')), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    assert 'temporarily unavailable' in resp.get_data(as_text=True)
    mock_upsert.assert_not_called()


def test_empty_seed_scrape_does_not_upsert(client):
    """A seed scrape that returns [] (empty cache, empty result) must NOT upsert —
    never clobber the cache with nothing."""
    with patch('brevethub.models.get_events_cache_freshness', return_value=None), \
         patch('brevethub.routes.calendar.get_rusa_events', return_value=[]), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert, \
         patch('brevethub.models.get_upcoming_events', return_value=[]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    assert 'temporarily unavailable' in resp.get_data(as_text=True)
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
