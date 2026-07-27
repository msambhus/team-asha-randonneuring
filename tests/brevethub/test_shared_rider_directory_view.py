from pathlib import Path

from shared.rider_directory_view import public_rider_row


ROOT = Path(__file__).resolve().parents[2]


def test_vendored_rider_directory_view_stays_identical():
    assert (
        ROOT / 'shared' / 'rider_directory_view.py'
    ).read_bytes() == (
        ROOT / 'brevethub' / 'shared' / 'rider_directory_view.py'
    ).read_bytes()


def test_public_rider_contract_normalizes_names_and_lifetime_metrics():
    row = public_rider_row({
        'id': 14,
        'first_name': 'Asha',
        'last_name': 'Rider',
        'rusa_id': 12345,
        'total_kms': 18750,
        'total_rides': 48,
        'permanent_count': 8,
        'populaire_count': 4,
        'rides_1000_plus': 3,
        'pbp_years': (2019, 2023),
        'pbp_count': 2,
        'eddington': 87,
    })

    assert row['display_name'] == 'Asha Rider'
    assert row['total_km'] == 18750
    assert row['count'] == 48
    assert row['permanent_count'] == 8
    assert row['populaire_count'] == 4
    assert row['rides_1000_plus'] == 3
    assert row['pbp_count'] == 2
    assert row['eddington'] == 87
