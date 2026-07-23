"""BrevetHub scheduled calendar refresh — /cron/refresh-calendar.

The heavy RUSA national-feed scrape lives here (off the request path) so /calendar
only reads a warm cache. These tests pin the contract:
  - auth: Bearer CRON_SECRET required; missing/wrong → 401; secret unset → 500,
  - a successful refresh scrapes + upserts every event and reports the count,
  - it is idempotent (ON CONFLICT upsert) and degrades gracefully (scrape failure
    and empty scrape both leave the cache intact and never 500),
  - the GET verb works (Vercel cron issues a GET),
  - the PINNED route: the production URL is exactly `/cron/refresh-calendar` — a
    regression guard fails the suite if a future prefix/decorator drift orphans the
    Vercel-scheduled refresh (missing prefix or double `/cron` prefix must 404).

All RUSA HTTP is mocked; no real DB or network (per BrevetHub test convention).
Follows the monkeypatch-models / `client`-fixture pattern in conftest.py.
"""
from unittest.mock import patch

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/refresh-calendar'

# Two scraped national-feed events (upsert is mocked, so only shape matters).
_SCRAPED = [
    {'route_id': '1234', 'name': 'Point Reyes Lighthouse 200', 'date': '2026-08-15',
     'distance_km': 200, 'region': 'CA: San Francisco', 'ride_type': 'ACP brevet',
     'elevation_ft': 4200, 'rwgps_url': None, 'start_location': None,
     'start_time': None, 'time_limit_hours': 13.5},
    {'route_id': '5678', 'name': 'Ferry Building 300', 'date': '2026-09-01',
     'distance_km': 300, 'region': 'CA: San Francisco', 'ride_type': 'ACP brevet',
     'elevation_ft': 9000, 'rwgps_url': None, 'start_location': None,
     'start_time': None, 'time_limit_hours': 20.0},
]


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


# --------------------------------------------------------------------------- #
# Auth ladder
# --------------------------------------------------------------------------- #
def test_refresh_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events') as mock_scrape:
        resp = client.post(_PATH)  # no Authorization header
    assert resp.status_code == 401
    mock_scrape.assert_not_called()


def test_refresh_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events') as mock_scrape:
        resp = client.post(_PATH, headers=_auth('wrong-secret'))
    assert resp.status_code == 401
    mock_scrape.assert_not_called()


def test_refresh_500_when_secret_unset(app, client):
    app.config['CRON_SECRET'] = None
    with patch('brevethub.routes.calendar.get_rusa_events') as mock_scrape:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500
    # Never runs an unauthenticated scrape when misconfigured.
    mock_scrape.assert_not_called()


# --------------------------------------------------------------------------- #
# Refresh behavior
# --------------------------------------------------------------------------- #
def test_refresh_scrapes_and_upserts_each_event(app, client):
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events', return_value=_SCRAPED) as mock_scrape, \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['refreshed'] == 2
    assert data['ok'] is True
    mock_scrape.assert_called_once()
    assert mock_upsert.call_count == 2


def test_refresh_get_verb_works(app, client):
    """Vercel cron issues a GET — the endpoint must accept it."""
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events', return_value=_SCRAPED), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert:
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['refreshed'] == 2
    assert mock_upsert.call_count == 2


def test_refresh_is_idempotent(app, client):
    """Re-running the refresh is safe (ON CONFLICT upsert) — same count each time."""
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events', return_value=_SCRAPED), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert:
        first = client.post(_PATH, headers=_auth())
        second = client.post(_PATH, headers=_auth())
    assert first.get_json()['refreshed'] == 2
    assert second.get_json()['refreshed'] == 2
    assert mock_upsert.call_count == 4  # 2 events × 2 runs, no error


def test_refresh_empty_scrape_does_not_upsert(app, client):
    """An empty scrape performs no upsert (never clobber the cache with nothing)."""
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events', return_value=[]), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['refreshed'] == 0
    mock_upsert.assert_not_called()


def test_refresh_scrape_failure_no_500(app, client):
    """A scrape exception is caught → non-500 JSON, cache left intact (no upsert)."""
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events', side_effect=OSError('rusa down')), \
         patch('brevethub.models.upsert_brevet_event') as mock_upsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is False
    mock_upsert.assert_not_called()


# --------------------------------------------------------------------------- #
# Route path regression guard (pinned — the redteam double-prefix / missing-prefix bug)
# --------------------------------------------------------------------------- #
def test_composed_route_is_exactly_cron_refresh_calendar(app):
    """The composed Flask URL must be exactly '/cron/refresh-calendar' (one /cron)."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert '/cron/refresh-calendar' in rules
    # The double-prefix bug would register this instead — must NOT exist.
    assert '/cron/cron/refresh-calendar' not in rules
    # The bare leaf (missing the blueprint prefix) must NOT be routable either.
    assert '/refresh-calendar' not in rules


def test_missing_and_double_prefix_paths_404(app, client):
    """The Vercel cron hits '/cron/refresh-calendar'; the wrong shapes must 404 so a
    future prefix/decorator drift fails the suite instead of silently orphaning the
    scheduled refresh."""
    _with_secret(app)
    with patch('brevethub.routes.calendar.get_rusa_events', return_value=_SCRAPED), \
         patch('brevethub.models.upsert_brevet_event'):
        assert client.post('/refresh-calendar', headers=_auth()).status_code == 404
        assert client.post('/cron/cron/refresh-calendar', headers=_auth()).status_code == 404
        # The pinned production path routes to the handler (not a 404).
        assert client.post(_PATH, headers=_auth()).status_code == 200
