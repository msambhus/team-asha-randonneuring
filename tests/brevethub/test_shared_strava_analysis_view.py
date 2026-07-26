from pathlib import Path

from shared.strava_analysis_view import build_team_asha_analysis_context
from shared.strava_analysis_index import ride_card, season_group


ROOT = Path(__file__).resolve().parents[2]


def test_vendored_strava_analysis_view_stays_identical():
    assert (
        ROOT / 'shared' / 'strava_analysis_view.py'
    ).read_bytes() == (
        ROOT / 'brevethub' / 'shared' / 'strava_analysis_view.py'
    ).read_bytes()

    assert (
        ROOT / 'shared' / 'strava_analysis_index.py'
    ).read_bytes() == (
        ROOT / 'brevethub' / 'shared' / 'strava_analysis_index.py'
    ).read_bytes()


def test_adapter_builds_team_asha_contract_without_product_data_access():
    analysis = {
        'activity': {
            'name': 'ACP 600K',
            'date': '2026-06-20',
            'distance_km': 602.4,
            'elapsed_time': '38h 30m',
            'moving_time': '31h 00m',
        },
        'summary': {'avg_hr': 128, 'avg_watts': 121},
        'comparison': {
            'summary': {
                'plan_distance_km': 600,
                'actual_distance_km': 602.4,
                'plan_total_time_min': 2400,
                'plan_break_time_min': 360,
            },
            'rows': [{
                'location': 'Control 1',
                'distance_miles': 50,
                'actual_elev_gain_ft': 1500,
            }],
        },
        'map': {
            'track': [[37.0, -122.0], [37.1, -122.1]],
            'stops': [{'distance_km': 80.5, 'lat': 37.1, 'lng': -122.1}],
        },
        'notes': {
            'overall': 'Steady pacing',
            'segments': {'Control 1': 'Good'},
            'stops': {'Control 1': 'Quick stop'},
        },
    }

    context = build_team_asha_analysis_context(
        analysis, activity_id=12345, rider_id=67890)

    assert context['ride']['name'] == 'ACP 600K'
    assert context['rider'] == {'rusa_id': 67890}
    assert context['comparison']['summary']['plan_distance_miles'] == 372.8
    assert context['comparison']['rows'][0]['actual_climb_ft_per_mi'] == 30
    assert context['comparison']['hr_power']['avg_hr'] == 128
    assert context['map_data']['stops'][0]['distance_miles'] == 50.0
    assert context['overall_note'] == 'Steady pacing'
    assert context['has_plan'] is True


def test_shared_index_contract_is_identical_for_both_products():
    card = ride_card(
        ride_id=42,
        ride_name='ACP 400K',
        date='2026-04-18',
        distance_km=400,
        has_match=True,
        is_brevet=True,
    )
    group = season_group({'name': '2025-2026'}, True, [card])

    assert group['is_current'] is True
    assert group['ride_cards'][0]['ride_id'] == 42
    assert group['ride_cards'][0]['has_match'] is True
    assert group['ride_cards'][0]['has_plan'] is False
    assert group['ride_cards'][0]['is_brevet'] is True
    assert group['ride_cards'][0]['activity'] is None


def test_private_index_includes_unmatched_regular_rides_newest_first(monkeypatch):
    from brevethub.routes import riders

    monkeypatch.setattr(riders, '_rider_finished_brevets', lambda _rider_id: [{
        'event_id': 9,
        'name': 'Official 200K',
        'date': '2026-07-10',
        'distance_km': 200,
        'finish_time': '12:30',
    }])
    monkeypatch.setattr(riders, '_plan_event_ids', lambda _brevets: {9})
    monkeypatch.setattr(
        riders,
        '_load_analysis_index_activities',
        lambda _rider_id, _connection: {
            101: {
                'id': 101,
                'name': 'Official 200K activity',
                'start_date_local': '2026-07-10T06:00:00',
                'distance': 200_500,
                'moving_time': 40_000,
                'elapsed_time': 45_000,
                'total_elevation_gain': 2_000,
                'average_speed': 5,
            },
            102: {
                'id': 102,
                'name': 'Morning training ride',
                'start_date_local': '2026-07-20T07:00:00',
                'distance': 50_000,
                'moving_time': 7_000,
                'elapsed_time': 7_500,
                'total_elevation_gain': 500,
                'average_speed': 7,
            },
        },
    )

    groups = riders._season_analysis_cards(1, {'id': 1})
    cards = groups[0]['ride_cards']

    assert [card['ride_id'] for card in cards] == [102, 101]
    assert cards[0]['is_brevet'] is False
    assert cards[0]['ride_name'] == 'Morning training ride'
    assert cards[1]['is_brevet'] is True
    assert cards[1]['has_plan'] is True
