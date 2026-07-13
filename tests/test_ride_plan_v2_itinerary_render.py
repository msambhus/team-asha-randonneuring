"""Render verification for the v2 itinerary table changes.

Drives the real /ride-plan/<slug>/v2 route (DB + wind mocked) through the real
`_to_v2_stops` and template, then asserts the rendered HTML satisfies the four
mission requirements:
  1. per-segment distance / time / speed surfaced (Dist + Pace columns),
  2. wind cell = arrow + mph only, no Head/Tail/Cross word,
  3. stop name ellipsizes with a hover tooltip (title attr),
  4. a per-segment toughness column reflecting climbing + headwind.
"""
from datetime import date
from unittest.mock import patch


_PLAN = {
    'id': 5,
    'slug': 'surf-city-test-600k',
    'name': 'Surf City Test 600K',
    'total_distance_miles': 130.0,
    'total_elevation_ft': 8000,
    'rwgps_url': 'https://ridewithgps.com/routes/12345',
    'rwgps_url_team': None,
    'start_time': '06:00',
}

_STOPS = [
    {'id': 1, 'location': 'Start', 'distance_miles': 0.0, 'elevation_gain': 0,
     'segment_time_min': 0, 'stop_duration_min': 0, 'stop_type': 'start',
     'notes': None, 'stop_order': 1},
    {'id': 2, 'location': 'A Very Long Control Name That Should Be Truncated, CA',
     'distance_miles': 60.0, 'elevation_gain': 4000, 'segment_time_min': 240,
     'stop_duration_min': 15, 'stop_type': 'control', 'notes': 'Lunch — refuel',
     'stop_order': 2},
    {'id': 3, 'location': 'Finish', 'distance_miles': 130.0, 'elevation_gain': 2000,
     'segment_time_min': 280, 'stop_duration_min': 0, 'stop_type': 'finish',
     'notes': None, 'stop_order': 3},
]

# Index-aligned per-stop wind; control has a headwind (hot), finish a tailwind.
_WIND = [
    None,
    {'wind_speed_mph': 12.0, 'wind_arrow_deg': 170, 'wind_type': 'headwind',
     'headwind_kmh': 16.1, 'temperature_f': 100, 'label': 'headwind'},
    {'wind_speed_mph': 8.0, 'wind_arrow_deg': 10, 'wind_type': 'tailwind',
     'headwind_kmh': -12.9, 'temperature_f': 60, 'label': 'tailwind'},
]


def _patches():
    return {
        'routes.riders.get_ride_plan_by_slug': lambda slug: dict(_PLAN),
        'routes.riders.get_ride_plan_stops': lambda pid: [dict(s) for s in _STOPS],
        'routes.riders.get_public_custom_plans': lambda pid: [],
        'routes.riders.fetch_route': lambda rid: {'track_points': [{'x': 0, 'y': 0, 'd': 0}]},
        # The v2 plan page now keys stored weather on the plan's next upcoming ride date.
        'routes.riders.get_upcoming_ride_date_for_plan': lambda pid: date(2026, 7, 20),
        'routes.riders.fetch_stop_wind': lambda **kw: list(_WIND),
        'routes.riders.get_user_by_id': lambda uid: None,
        'models.get_latest_ride_for_plan': lambda pid: None,
        'models.get_upcoming_rusa_events': lambda: [],
        'models.get_signups_for_ride': lambda eid: [],
        'models.get_user_by_id': lambda uid: None,
    }


def _render(client):
    mgrs = [patch(path, side_effect=val) for path, val in _patches().items()]
    for m in mgrs:
        m.start()
    try:
        return client.get('/ride-plan/surf-city-test-600k/v2')
    finally:
        for m in mgrs:
            m.stop()


def _itinerary_table(html):
    """Slice out just the itinerary <table> so assertions don't pick up the
    snapshot card / journey SVG (which still use wind labels in tooltips)."""
    start = html.index('id="rpv2-itinerary"')
    end = html.index('</table>', start)
    return html[start:end]


def test_itinerary_renders_new_columns(client):
    resp = _render(client)
    assert resp.status_code == 200
    html = resp.data.decode()
    # Seg + Cumul distance, Climb, Pace, Elapsed time, and Tuf columns present.
    for header in ('>Seg</th>', '>Cumul</th>', '>Climb</th>', '>Pace</th>',
                   '>Elapsed</th>', '>Tuf<'):
        assert header in html, f"missing header {header}"
    # Old combined Dist column and the Break column are gone.
    assert '>Dist</th>' not in html
    assert '>Break</th>' not in html


def test_segment_distance_time_speed_surfaced(client):
    table = _itinerary_table(_render(client).data.decode())
    assert 'rpv2-pace' in table         # segment time · speed cell
    # 60 mi over 4h -> 15.0 mph implied speed shown in the Pace cell.
    assert '15.0' in table


def test_cumulative_time_column_present(client):
    table = _itinerary_table(_render(client).data.decode())
    # Finish cumulative elapsed = 535 min arrival -> "8h55".
    assert '8h55' in table


def test_wind_cell_has_arrow_no_classification_word(client):
    table = _itinerary_table(_render(client).data.decode())
    assert 'rpv2-wind-arrow' in table
    # The old visible "<Label> <mph>" format must be gone from the table cell.
    assert 'Head 12.0' not in table
    assert 'Tail 8.0' not in table
    # Classification is preserved on hover via the title attribute.
    assert 'title="Head' in table or 'title="Tail' in table


def test_stop_cell_has_whole_cell_hover_tooltip(client):
    table = _itinerary_table(_render(client).data.decode())
    # Tooltip is on the whole Stop <td> so hovering anywhere in the cell
    # (not only the text) reveals the full name (+ note).
    assert 'class="rpv2-stopcell" title=' in table


def test_toughness_column_reflects_climb_headwind_temp(client):
    table = _itinerary_table(_render(client).data.decode())
    # Control: 67 ft/mi + 10mph headwind (150 equiv) -> base capped 8.5,
    # + 100F heat (+2.0) -> clamped to 10.0, red tier t4.
    assert 'rpv2-tough t4' in table
    assert '10.0' in table


def test_tuf_header_has_help_bubble(client):
    html = _render(client).data.decode()
    # The "?" info bubble explaining the toughness score is present in the header.
    assert 'rpv2-help' in html
    assert 'rpv2-help-bub' in html
    assert 'Toughness' in html


def test_start_row_renders_dashes_for_segment_metrics(client):
    table = _itinerary_table(_render(client).data.decode())
    # The 0-length start segment has no speed/toughness -> the guarded
    # template branches render the "unknown" placeholders + em dashes.
    start_row = table[table.index('data-stop-i="0"'):table.index('data-stop-i="1"')]
    assert 'rpv2-tough unknown' in start_row
    assert 'rpv2-wind unknown' in start_row
    assert '—' in start_row
