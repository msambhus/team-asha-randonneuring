"""BrevetHub design-system smoke test (Mission 10 — styling cleanup).

Two guards, both DB-free / network-free per the established BrevetHub test pattern:

1. **Stylesheet contract** — the shared design-system classes and element ids that
   every template references are actually DEFINED in ``static/style.css``. This is
   the regression guard that the three feature pages bolted on during M8–M9 (plan,
   analysis, live-map) never render as raw, unstyled HTML again.

2. **Render-path contract** — each key page returns 200, links ``style.css``, and
   contains its expected component class(es) in the rendered markup. A render (not
   a Jinja parse check) is the only thing that catches a missing-filter 500, so the
   plan/analysis/live pages are exercised through the real client with mocked models.
"""
import os
import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STYLE_CSS = os.path.join(REPO_ROOT, 'brevethub', 'static', 'style.css')


_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': None,
          'rusa_id_duplicate': False,
          'created_at': datetime(2024, 3, 1, tzinfo=timezone.utc)}

_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes Lighthouse 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200, 'rwgps_url': None,
    'start_location': None, 'start_time': None, 'time_limit_hours': 13.5,
}

# A fully-populated cached analysis (mirrors test_analysis.py) so the detail page
# renders its map + segment/stop tables, exercising the newly-defined classes.
_ANALYSIS = {
    'activity': {'name': 'Coastal 200', 'date': '2026-06-20', 'distance_km': 203.4,
                 'elevation_ft': 6800, 'moving_time': '9h 12m',
                 'elapsed_time': '11h 40m', 'avg_speed_kmh': 22.1},
    'summary': {'moving_speed_kmh': 23.4, 'avg_hr': 138, 'max_hr': 171,
                'avg_watts': 165, 'max_watts': 520},
    'stop_count': 2,
    'stops': [{'distance_km': 100.0, 'duration_min': 18.0, 'lat': 37.5, 'lng': -122.3}],
    'legs': [{'to_km': 100.0, 'distance_km': 100.0, 'riding_time': '4h 30m',
              'speed_kmh': 22.2, 'avg_hr': 140, 'avg_watts': 170, 'np_watts': 178,
              'avg_cadence': 84, 'grade_pct': 1.2, 'climb_ft_per_mi': 45}],
    'map': {'track': [[37.5, -122.3], [37.6, -122.4]],
            'bounds': [[37.5, -122.4], [37.6, -122.3]]},
}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# 1. Stylesheet contract — every referenced component class/id is defined.
# --------------------------------------------------------------------------- #
def _css():
    with open(STYLE_CSS, 'r', encoding='utf-8') as fh:
        return fh.read()


# Selectors the templates rely on. A missing one means a page renders unstyled.
REQUIRED_SELECTORS = [
    # Core design-system components (base + shared).
    '.container', '.card', '.btn', '.badge', '.form', '.flash', '.empty-state',
    '.site-header', '.nav',
    # Table shells.
    '.rusa-history', '.signups-table', '.live-rides',
    # Plan page (M8) — previously undefined.
    '.plan-page', '.plan-intro', '.plan-event-meta', '.plan-target-form',
    '.plan-summary', '.plan-schedule', '.plan-bank-ok', '.plan-bank-low',
    '.plan-save-section',
    # Analysis + live (M9) — previously undefined.
    '.analysis-list', '.analysis-legs', '.analysis-stops',
    '#analysis-map', '#live-map', '.live-timeline',
]


@pytest.mark.parametrize('selector', REQUIRED_SELECTORS)
def test_design_system_selector_defined(selector):
    css = _css()
    # A defined rule is the selector followed (possibly after other selectors in a
    # group, or a combinator) by an opening brace somewhere in the file.
    assert re.search(re.escape(selector) + r'[\s,:.#\w>()\-\[\]="\']*\{', css), \
        f'{selector} is referenced by a template but not defined in style.css'


def test_no_dead_calendar_table_rule():
    """The dead ``.calendar-table`` selector was removed (no template uses it)."""
    assert '.calendar-table' not in _css()


def test_root_tokens_present():
    """The neutral palette is still driven entirely by :root variables."""
    css = _css()
    for token in ('--bg', '--surface', '--text', '--border', '--accent',
                  '--success', '--warning', '--danger'):
        assert token in css, f'design token {token} missing from :root'


# --------------------------------------------------------------------------- #
# 2. Render-path contract — each key page: 200, links style.css, has its class.
# --------------------------------------------------------------------------- #
def test_landing_styled(client):
    resp = client.get('/')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'hero' in body


def test_login_styled(client):
    resp = client.get('/auth/login')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body


def test_calendar_styled(client):
    with patch('brevethub.models.get_brevet_weather_for_events', return_value={}), \
         patch('brevethub.models.get_events_cache_freshness',
               return_value=datetime.now(timezone.utc)), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'events-grid' in body and 'event-card' in body


def test_plan_styled(client):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT):
        resp = client.get('/plan/11?speed=20')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    # The plan-page classes that were undefined before this mission now render.
    assert 'plan-page' in body and 'plan-schedule' in body


def test_dashboard_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_signups', return_value=[]):
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body
    # No sign-ups -> the empty-state component renders (text preserved).
    assert 'empty-state' in body
    assert "haven't signed up for any upcoming brevets yet" in body


def test_profile_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/profile')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body and 'profile' in body


def test_analysis_list_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body


def test_analysis_detail_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride_analysis',
               return_value={'analysis': _ANALYSIS, 'activity_streams': b'x',
                             'computed_at': None}):
        resp = client.get('/analysis/555')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    # The segment/stop tables + map container use the newly-defined classes.
    assert 'analysis-legs' in body and 'analysis-stops' in body
    assert 'analysis-map' in body
    # The inline <style> block was removed — the map is sized from style.css now.
    assert '<style>' not in body


def test_live_list_styled(client):
    with patch('brevethub.models.get_public_rides', return_value=[]):
        resp = client.get('/live')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    # Empty state renders for no public rides.
    assert 'empty-state' in body


def test_live_map_styled(client):
    ride = {'id': 1, 'name': 'SFR Point Reyes 200k', 'club_name': None,
            'distance_km': 200, 'start_at': None, 'status': 'live'}
    with patch('brevethub.models.get_public_ride', return_value=ride):
        resp = client.get('/live/1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'live-map' in body and 'live-timeline' in body
    # The inline <style> block was moved into style.css.
    assert '<style>' not in body
