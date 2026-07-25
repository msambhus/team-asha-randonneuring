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
    )
    group = season_group({'name': '2025-2026'}, True, [card])

    assert group['is_current'] is True
    assert group['ride_cards'][0]['ride_id'] == 42
    assert group['ride_cards'][0]['has_match'] is True
    assert group['ride_cards'][0]['has_plan'] is False
    assert group['ride_cards'][0]['activity'] is None
