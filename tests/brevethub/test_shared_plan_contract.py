from pathlib import Path

from shared.plan_view import plan_header, plan_stop


ROOT = Path(__file__).resolve().parents[2]


def test_vendored_plan_view_stays_identical():
    assert (
        ROOT / 'shared' / 'plan_view.py'
    ).read_bytes() == (
        ROOT / 'brevethub' / 'shared' / 'plan_view.py'
    ).read_bytes()


def test_plan_header_preserves_product_fields_and_common_defaults():
    plan = plan_header({
        'id': 9,
        'name': 'ACP 600K',
        'rwgps_url': 'https://ridewithgps.com/routes/123',
        'total_distance_miles': 374.2,
        'club_specific_setting': 'preserved',
    })

    assert plan['name'] == 'ACP 600K'
    assert plan['start_time'] == '06:00'
    assert plan['variant'] == 'conservative'
    assert plan['rwgps_url'].endswith('/123')
    assert plan['club_specific_setting'] == 'preserved'


def test_plan_stop_normalizes_location_name_without_losing_enrichment():
    stop = plan_stop({
        'stop_order': 2,
        'location': 'Control 1',
        'distance_miles': 52.4,
        'wind': {'wind_speed_mph': 12},
        'difficulty_color': '#f59e0b',
    })

    assert stop['name'] == 'Control 1'
    assert stop['stop_type'] == 'waypoint'
    assert stop['stop_duration_min'] == 0
    assert stop['wind']['wind_speed_mph'] == 12
    assert stop['difficulty_color'] == '#f59e0b'
