"""RUSA brevet stats + dashboard rendering (mocked scrape, no real DB / network).

Covers the pure stats computation, the JSON-safe normalization, and the
cache-aware, failure-tolerant dashboard assembly: cache hit (scraper not called)
vs miss (called once) vs POST /rusa/refresh (always called), a scrape failure
degrading without a 500, and the add-RUSA-ID prompt when the rider has none.
"""
from datetime import date, datetime, timezone
from unittest.mock import patch

from brevethub import rusa_stats

# A rider with a RUSA ID and a completed profile.
_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': '12345', 'club_id': None,
          'rusa_id_duplicate': False}
_RIDER_NO_RUSA = {**_RIDER, 'rusa_id': None}

# A cached, already-normalized brevet list (dates as ISO strings).
_CACHED = [
    {'date': '2025-05-10', 'distance_km': 200, 'finish_time': '13:30', 'route_name': 'Spring 200'},
    {'date': '2025-06-14', 'distance_km': 300, 'finish_time': '19:45', 'route_name': 'Summer 300'},
]


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Pure stats computation
# --------------------------------------------------------------------------- #
def test_normalize_results_converts_dates_to_iso():
    raw = [{'date': date(2025, 5, 10), 'distance_km': 200, 'finish_time': '13:30',
            'route_name': 'Spring 200'}]
    out = rusa_stats.normalize_results(raw)
    assert out == [{'date': '2025-05-10', 'distance_km': 200,
                    'finish_time': '13:30', 'route_name': 'Spring 200'}]


def test_compute_stats_totals_bands_and_longest():
    brevets = [
        {'date': '2025-01-01', 'distance_km': 200},
        {'date': '2025-02-01', 'distance_km': 300},
        {'date': '2025-03-01', 'distance_km': 400},
        {'date': '2025-04-01', 'distance_km': 600},
        {'date': '2025-05-01', 'distance_km': 1000},
    ]
    stats = rusa_stats.compute_stats(brevets, current_year=2025)
    assert stats['total_km'] == 2500
    assert stats['count'] == 5
    assert stats['bands'] == {'200': 1, '300': 1, '400': 1, '600': 1, '1000': 1}
    assert stats['longest_km'] == 1000
    assert stats['season_total_km'] == 2500
    assert stats['season_count'] == 5


def test_compute_stats_detects_sr_within_calendar_year():
    brevets = [
        {'date': '2024-03-01', 'distance_km': 200},
        {'date': '2024-04-01', 'distance_km': 300},
        {'date': '2024-05-01', 'distance_km': 400},
        {'date': '2024-06-01', 'distance_km': 600},
    ]
    stats = rusa_stats.compute_stats(brevets, current_year=2025)
    assert stats['is_sr'] is True
    assert stats['sr_year'] == 2024
    # 2025 season has none of these
    assert stats['season_count'] == 0


def test_compute_stats_no_sr_when_series_incomplete():
    brevets = [
        {'date': '2025-03-01', 'distance_km': 200},
        {'date': '2025-04-01', 'distance_km': 300},
        {'date': '2025-05-01', 'distance_km': 400},
        # no 600 → not an SR
    ]
    stats = rusa_stats.compute_stats(brevets, current_year=2025)
    assert stats['is_sr'] is False
    assert stats['sr_year'] is None


# --------------------------------------------------------------------------- #
# Dashboard assembly (cache-aware, failure-tolerant)
# --------------------------------------------------------------------------- #
def test_dashboard_renders_cached_history_without_scraping(client):
    """Fresh cache → history + stats render and the scraper is NOT called."""
    _login(client)
    fresh = datetime.now(timezone.utc)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': _CACHED, 'rusa_fetched_at': fresh}), \
         patch('brevethub.routes.main.fetch_rider_results') as mock_scrape:
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Spring 200' in body
    assert 'Summer 300' in body
    assert '500 km' in body  # total_km of the two cached brevets
    mock_scrape.assert_not_called()


def test_dashboard_cache_miss_scrapes_once_and_caches(client):
    """No cached data → scrape exactly once and persist the result."""
    _login(client)
    raw = [{'date': date(2025, 5, 10), 'distance_km': 200, 'finish_time': '13:30',
            'route_name': 'Spring 200'}]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': None, 'rusa_fetched_at': None}), \
         patch('brevethub.models.update_rider_rusa_cache') as mock_store, \
         patch('brevethub.routes.main.fetch_rider_results', return_value=raw) as mock_scrape:
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    mock_scrape.assert_called_once_with('12345')
    mock_store.assert_called_once()
    assert 'Spring 200' in resp.get_data(as_text=True)


def test_refresh_endpoint_always_scrapes(client):
    """POST /rusa/refresh re-scrapes even when the cache is fresh."""
    _login(client)
    fresh = datetime.now(timezone.utc)
    raw = [{'date': date(2025, 6, 1), 'distance_km': 400, 'finish_time': '26:00',
            'route_name': 'New 400'}]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': _CACHED, 'rusa_fetched_at': fresh}), \
         patch('brevethub.models.update_rider_rusa_cache'), \
         patch('brevethub.routes.main.fetch_rider_results', return_value=raw) as mock_scrape:
        resp = client.post('/rusa/refresh')
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'].endswith('/dashboard')
    mock_scrape.assert_called_once_with('12345')


def test_refresh_empty_scrape_keeps_cache_and_reports_failure(client):
    """A forced refresh that comes back empty (a silent scraper/network failure)
    must NOT overwrite the existing cache and must flash a failure message —
    never 'RUSA history refreshed'."""
    _login(client)
    fresh = datetime.now(timezone.utc)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': _CACHED, 'rusa_fetched_at': fresh}), \
         patch('brevethub.models.update_rider_rusa_cache') as mock_store, \
         patch('brevethub.routes.main.fetch_rider_results', return_value=[]) as mock_scrape:
        resp = client.post('/rusa/refresh', follow_redirects=True)
    assert resp.status_code == 200
    mock_scrape.assert_called_once_with('12345')
    mock_store.assert_not_called()  # cache preserved, not overwritten with empty
    body = resp.get_data(as_text=True)
    assert 'cached history' in body           # failure message flashed
    assert 'RUSA history refreshed' not in body
    assert 'Spring 200' in body               # cached history still shown


def test_dashboard_scrape_failure_does_not_500(client):
    """A scraper exception with no cache → 200 with an explanatory message."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': None, 'rusa_fetched_at': None}), \
         patch('brevethub.routes.main.fetch_rider_results',
               side_effect=Exception('RUSA down')):
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    assert 'Could not reach RUSA' in resp.get_data(as_text=True)


def test_dashboard_empty_results_shows_unavailable_message(client):
    """The scraper reporting no rows (empty list) with no cache → soft message."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': None, 'rusa_fetched_at': None}), \
         patch('brevethub.routes.main.fetch_rider_results', return_value=[]):
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    assert 'No RUSA results found' in resp.get_data(as_text=True)


def test_dashboard_prompts_when_no_rusa_id(client):
    """No RUSA ID → an add-ID prompt and the scraper is never called."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER_NO_RUSA), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.routes.main.fetch_rider_results') as mock_scrape:
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    assert 'Add a RUSA ID' in resp.get_data(as_text=True)
    mock_scrape.assert_not_called()
