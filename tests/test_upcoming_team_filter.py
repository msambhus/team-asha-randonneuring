"""Team Asha rides on the /upcoming calendar (hidden behind a "Team Asha" filter).

The upcoming page historically listed only external RUSA-club events (is_team_ride
filtered out). Team Asha club rides now flow through the same list so they reuse all
per-event processing, but are rendered hidden by default and revealed only by the
"Team Asha" club filter button. These tests pin:
  - the route includes Team Asha rides in the rendered event list;
  - a Team Asha ride's own (possibly non-standard) distance never adds a distance
    filter button;
  - the template ships the Team Asha filter button + the team-aware filter JS.
"""
import os
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'templates', 'upcoming_brevets.html')

_SEASON = {'id': 3, 'name': '2025-2026'}


def _event(**over):
    """A complete upcoming-event row (mirrors get_all_upcoming_events output)."""
    base = {
        'id': 120, 'season_id': 3, 'club_id': 1, 'name': 'Winters 200k',
        'ride_type': 'Permanent', 'date': date(2026, 8, 15), 'date_str': '2026-08-15',
        'distance_km': 200, 'elevation_ft': 4000, 'distance_miles': 124.0,
        'ft_per_mile': 32.0, 'rwgps_url': None, 'rwgps_url_team': None,
        'route_name': 'Winters 200k', 'club_code': 'SFR',
        'club_name': 'San Francisco Randonneurs', 'region': 'San Francisco',
        'plan_slug': None, 'plan_rwgps_url_team': None, 'plan_start_time': None,
        'plan_avg_speed': None, 'start_time': '07:00', 'time_limit_hours': 13.5,
        'is_team_ride': False, 'signup_count': 0, 'event_status': 'UPCOMING',
    }
    base.update(over)
    return base


def _render_upcoming(client, events, plans=None, plan_stops_mock=None):
    """Render /riders/2025-2026/upcoming as a guest, capturing the template context
    instead of rendering the (large) template, with everything else mocked."""
    captured = {}

    def _capture(template, **ctx):
        captured['template'] = template
        captured.update(ctx)
        return ''

    stops_mock = plan_stops_mock or MagicMock(return_value=[])
    with patch('routes.riders.get_season_by_name', return_value=_SEASON), \
         patch('routes.riders.get_current_season', return_value=_SEASON), \
         patch('routes.riders.get_all_upcoming_events', return_value=events), \
         patch('routes.riders.get_rides_for_season', return_value=[]), \
         patch('routes.riders.get_all_ride_plans', return_value=(plans or [])), \
         patch('routes.riders.get_ride_plan_stops', stops_mock), \
         patch('routes.riders.fetch_stop_wind', return_value=None), \
         patch('routes.riders.get_signup_counts_batch', return_value={}), \
         patch('models.get_completed_events_for_season', return_value=[]), \
         patch('routes.riders.render_template', side_effect=_capture):
        resp = client.get('/riders/2025-2026/upcoming')
    assert resp.status_code == 200
    return captured


def test_team_ride_included_in_rendered_events(client):
    """A Team Asha ride is passed to the template alongside the external events (so
    it can render as a hidden card), carrying its is_team_ride flag."""
    team = _event(id=193, name='Heart of the Valley 200k', club_code='TA',
                  club_name='Team Asha', region='Team Asha', distance_km=205,
                  is_team_ride=True, date=date(2026, 7, 25), date_str='2026-07-25')
    ctx = _render_upcoming(client, [_event(), team])
    rendered = {e['id']: e for e in ctx['rusa_events']}
    assert 193 in rendered, "Team Asha ride must be in the rendered event list"
    assert rendered[193]['is_team_ride'] is True
    assert rendered[120]['is_team_ride'] is False


def test_team_ride_distance_excluded_from_distance_filter(client):
    """A Team Asha ride's own distance (e.g. a 205 km perm) never adds a distance
    filter button — team rides are reached via the club filter, not distance."""
    team = _event(id=193, distance_km=205, is_team_ride=True)
    ctx = _render_upcoming(client, [_event(distance_km=200), team])
    assert 205 not in ctx['distances']
    assert 200 in ctx['distances']


def test_has_external_events_flag(client):
    """The empty-state flag reflects EXTERNAL events only, so a page carrying just
    hidden team rides still shows the 'no RUSA events' message."""
    team = _event(id=193, club_code='TA', region='Team Asha', is_team_ride=True)
    # External present → flag True
    ctx = _render_upcoming(client, [_event(), team])
    assert ctx['has_external_events'] is True
    # Only team rides → flag False (empty-state must show)
    ctx2 = _render_upcoming(client, [team])
    assert ctx2['has_external_events'] is False


def test_minnesota_randonneurs_has_calendar_filter(client):
    """Minnesota events receive a named club filter and the matching card region."""
    minnesota = _event(
        id=194,
        name='Coulee Challenge',
        club_code='MNR',
        club_name='Minnesota Randonneurs',
        region='Minnesota',
        distance_km=1200,
    )
    ctx = _render_upcoming(client, [minnesota])
    assert 'Minnesota' in ctx['region_colors']
    assert ctx['rusa_events'][0]['region'] == 'Minnesota'
    assert 1200 in ctx['distances']


def test_team_ride_skipped_in_wind_warning_loop(client):
    """A Team Asha ride is skipped by the wind-warning loop even when it has a linked
    plan + near date — the banner is ungated, so a hidden ride must never raise it."""
    near = date.today() + timedelta(days=7)
    plans = [{'slug': 'heart-of-the-valley', 'id': 99,
              'name': 'Heart of the Valley 200k', 'rwgps_url_team': None}]
    team = _event(id=193, name='Heart of the Valley 200k', route_name='Heart of the Valley 200k',
                  club_code='TA', region='Team Asha', is_team_ride=True,
                  date=near, date_str=near.isoformat(), plan_slug='heart-of-the-valley',
                  rwgps_url='https://ridewithgps.com/routes/34227438')
    stops_mock = MagicMock(return_value=[])
    ctx = _render_upcoming(client, [team], plans=plans, plan_stops_mock=stops_mock)
    # The loop must have skipped the team ride before touching its plan stops.
    stops_mock.assert_not_called()
    assert ctx['wind_warnings'] == []


def test_template_has_team_asha_filter_and_js():
    """The template ships the Team Asha club-filter button, flags team cards, and the
    team-aware filter logic that hides them by default."""
    with open(TEMPLATE_PATH, encoding='utf-8') as fh:
        src = fh.read()
    # The dedicated club-filter button.
    assert 'data-region="team-asha"' in src
    assert "filterByRegion('team-asha')" in src
    # Cards flag team rides and start hidden.
    assert "data-team-ride=\"{{ 'true' if event.is_team_ride else 'false' }}\"" in src
    assert '{% if event.is_team_ride %} hidden{% endif %}' in src
    # applyFilters hides team rides under every non-team filter (incl. "all").
    assert "activeRegion === 'team-asha'" in src
    assert 'regionMatch = !isTeam;' in src
    # Default view is enforced on load.
    assert 'applyFilters();   // enforce the default view' in src
