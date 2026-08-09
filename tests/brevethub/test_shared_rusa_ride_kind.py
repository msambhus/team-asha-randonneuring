from datetime import date
from pathlib import Path

from shared.rusa_ride_kind import ride_kind_counts, rusa_ride_kind
from shared.seasons import career_summary, earliest_brevet_date, seasons_with_summaries


ROOT = Path(__file__).resolve().parents[2]


def test_vendored_rusa_ride_kind_and_seasons_stay_identical():
    for relative in ('rusa_ride_kind.py', 'seasons.py'):
        assert (
            ROOT / 'shared' / relative
        ).read_bytes() == (
            ROOT / 'brevethub' / 'shared' / relative
        ).read_bytes()


def test_rusa_ride_kinds_are_counted_and_annotated():
    rides = [
        {'date': '2026-07-01', 'distance_km': 200,
         'event_type': 'ACP brevet', 'route_name': 'Summer 200'},
        {'date': '2026-06-01', 'distance_km': 200,
         'event_type': 'RUSA Permanent', 'route_name': 'Permanent 123'},
        {'date': '2026-05-01', 'distance_km': 120,
         'event_type': 'RUSA populaire', 'route_name': 'Spring Populaire'},
    ]

    assert [rusa_ride_kind(ride) for ride in rides] == [
        'brevet', 'permanent', 'populaire']
    assert ride_kind_counts(rides) == {
        'brevet': 1, 'permanent': 1, 'populaire': 1}

    groups = seasons_with_summaries(rides, date(2026, 7, 15))
    assert [ride['ride_kind'] for ride in groups[0]['brevets']] == [
        'brevet', 'permanent', 'populaire']

    career = career_summary(rides, date(2026, 7, 15))
    assert career['count'] == 3
    assert career['total_km'] == 520
    assert career['brevet_count'] == 1
    assert career['permanent_count'] == 1
    assert career['populaire_count'] == 1


def test_earliest_brevet_date_uses_oldest_ride():
    rides = [
        {'date': '2026-07-01', 'distance_km': 200},
        {'date': '2021-11-06', 'distance_km': 200},
        {'date': 'bad-date', 'distance_km': 300},
    ]
    assert earliest_brevet_date(rides) == date(2021, 11, 6)
    assert earliest_brevet_date([]) is None
