"""BrevetHub scheduled weather warmer — /cron/fetch-brevet-weather.

The Open-Meteo point-forecast fetch lives here (off the request path) so /calendar
only reads a warm rp_brevet_weather cache. These tests pin the contract:
  - auth: Bearer CRON_SECRET required; missing/wrong → 401; secret unset → 500,
  - a successful run resolves each event's region, fetches, and upserts, counting
    fetched/skipped/failed,
  - an unresolved region and a beyond-horizon (empty fetch) are SKIPPED, not upserted,
  - a per-event fetch error fails SOFT (counted, no 500, no upsert) and the run keeps
    going for the other events,
  - it is idempotent (ON CONFLICT upsert) — same counts on a repeat run,
  - a target-load failure degrades to non-500 JSON,
  - the PINNED route: the production URL is exactly `/cron/fetch-brevet-weather` — a
    regression guard fails the suite if a future prefix/decorator drift orphans the
    Vercel-scheduled warmer (missing prefix or double `/cron` prefix must 404).

All Open-Meteo HTTP is mocked (via the route's imported fetch_point_forecast); no
real DB or network. Follows the monkeypatch-models / `client`-fixture pattern in
conftest.py and mirrors test_cron.py.
"""
from datetime import date, timedelta
from unittest.mock import patch

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/fetch-brevet-weather'

_D1 = date.today() + timedelta(days=3)
_D2 = date.today() + timedelta(days=6)

# Two near-term targets with resolvable CA regions.
_TARGETS = [
    {'id': 11, 'date': _D1, 'region': 'CA: San Francisco'},
    {'id': 12, 'date': _D2, 'region': 'CA: Davis'},
]

_RAW_FORECAST = {'daily': {'time': ['2026-08-15'], 'weather_code': [1],
                           'temperature_2m_max': [24.0], 'temperature_2m_min': [11.0],
                           'precipitation_sum': [0.0], 'precipitation_probability_max': [5],
                           'wind_speed_10m_max': [12.0], 'wind_direction_10m_dominant': [300]}}


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


# --------------------------------------------------------------------------- #
# Auth ladder
# --------------------------------------------------------------------------- #
def test_weather_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets') as mtargets:
        resp = client.post(_PATH)  # no Authorization header
    assert resp.status_code == 401
    mtargets.assert_not_called()


def test_weather_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets') as mtargets:
        resp = client.post(_PATH, headers=_auth('wrong-secret'))
    assert resp.status_code == 401
    mtargets.assert_not_called()


def test_weather_500_when_secret_unset(app, client):
    app.config['CRON_SECRET'] = None
    with patch('brevethub.models.get_weather_forecast_targets') as mtargets:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500
    mtargets.assert_not_called()


# --------------------------------------------------------------------------- #
# Fetch behavior
# --------------------------------------------------------------------------- #
def test_weather_fetches_and_upserts_each_event(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_point_forecast', return_value=_RAW_FORECAST) as mfetch, \
         patch('brevethub.models.upsert_brevet_weather') as mupsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['fetched'] == 2 and data['skipped'] == 0 and data['failed'] == 0
    assert data['considered'] == 2
    assert mfetch.call_count == 2
    assert mupsert.call_count == 2
    # Upsert is keyed by (event_id, forecast_date) with the raw payload.
    (event_id, forecast_date, payload), _ = mupsert.call_args
    assert event_id in (11, 12)
    assert payload == _RAW_FORECAST


def test_weather_get_verb_works(app, client):
    """Vercel cron issues a GET — the endpoint must accept it."""
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_point_forecast', return_value=_RAW_FORECAST), \
         patch('brevethub.models.upsert_brevet_weather') as mupsert:
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['fetched'] == 2
    assert mupsert.call_count == 2


def test_weather_skips_unresolved_region(app, client):
    """A region with no known coordinate is skipped — no fetch, no upsert."""
    _with_secret(app)
    targets = [{'id': 21, 'date': _D1, 'region': 'ON: Toronto'},   # foreign → unresolved
               {'id': 22, 'date': _D1, 'region': None}]            # no region
    with patch('brevethub.models.get_weather_forecast_targets', return_value=targets), \
         patch('brevethub.routes.cron.fetch_point_forecast') as mfetch, \
         patch('brevethub.models.upsert_brevet_weather') as mupsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['skipped'] == 2 and data['fetched'] == 0
    mfetch.assert_not_called()
    mupsert.assert_not_called()


def test_weather_skips_beyond_horizon_empty_fetch(app, client):
    """fetch_point_forecast returns None (beyond horizon / empty) → skip, no upsert."""
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_point_forecast', return_value=None), \
         patch('brevethub.models.upsert_brevet_weather') as mupsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['skipped'] == 2 and data['fetched'] == 0
    mupsert.assert_not_called()


def test_weather_fetch_error_fails_soft_per_event(app, client):
    """A per-event fetch exception is counted as a failure, never 500s, never upserts
    that event, and does not stop the run for the other events."""
    _with_secret(app)

    def _fetch(lat, lng, when):
        if when == _D1:
            raise OSError('open-meteo down')
        return _RAW_FORECAST

    with patch('brevethub.models.get_weather_forecast_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_point_forecast', side_effect=_fetch), \
         patch('brevethub.models.upsert_brevet_weather') as mupsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['failed'] == 1 and data['fetched'] == 1
    # Only the successful event was upserted; the failed one kept its last-good row.
    assert mupsert.call_count == 1
    (event_id, _fd, _payload), _ = mupsert.call_args
    assert event_id == 12


def test_weather_is_idempotent(app, client):
    """Re-running is safe (ON CONFLICT upsert) — same counts each time."""
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_point_forecast', return_value=_RAW_FORECAST), \
         patch('brevethub.models.upsert_brevet_weather') as mupsert:
        first = client.post(_PATH, headers=_auth())
        second = client.post(_PATH, headers=_auth())
    assert first.get_json()['fetched'] == 2
    assert second.get_json()['fetched'] == 2
    assert mupsert.call_count == 4   # 2 events × 2 runs


def test_weather_target_load_failure_no_500(app, client):
    """A target-load DB error is caught → non-500 JSON, nothing fetched/upserted."""
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets', side_effect=OSError('db down')), \
         patch('brevethub.routes.cron.fetch_point_forecast') as mfetch, \
         patch('brevethub.models.upsert_brevet_weather') as mupsert:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is False
    mfetch.assert_not_called()
    mupsert.assert_not_called()


# --------------------------------------------------------------------------- #
# Route path regression guard (pinned — the double-prefix / missing-prefix bug)
# --------------------------------------------------------------------------- #
def test_composed_route_is_exactly_cron_fetch_brevet_weather(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert '/cron/fetch-brevet-weather' in rules
    assert '/cron/cron/fetch-brevet-weather' not in rules   # double-prefix bug
    assert '/fetch-brevet-weather' not in rules             # missing-prefix bug


def test_missing_and_double_prefix_paths_404(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_weather_forecast_targets', return_value=_TARGETS), \
         patch('brevethub.routes.cron.fetch_point_forecast', return_value=_RAW_FORECAST), \
         patch('brevethub.models.upsert_brevet_weather'):
        assert client.post('/fetch-brevet-weather', headers=_auth()).status_code == 404
        assert client.post('/cron/cron/fetch-brevet-weather', headers=_auth()).status_code == 404
        assert client.post(_PATH, headers=_auth()).status_code == 200
