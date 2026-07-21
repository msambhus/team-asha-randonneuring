"""Eddington engine behaviour, exercised through the canonical shared.eddington module.

E is the largest number E such that the rider has ridden >= E units (km or miles)
on >= E different days. These tests pin the math the whole feature reuses: the
unit conversion (meters -> miles/km), the CYCLING_TYPES filter on the
``activity_type`` key, distinct-day counting, and the graceful empty result.
"""
from shared import eddington


def _act(distance_m, start_date, activity_type='Ride', **kwargs):
    """A raw-ish activity dict shaped like the engine expects: distance in METERS,
    an ISO ``start_date``, and the ``activity_type`` filter key. Accepts **kwargs so
    callers can pass extra Strava fields without breaking."""
    a = {'distance': distance_m, 'start_date': start_date, 'activity_type': activity_type}
    a.update(kwargs)
    return a


def test_empty_history_is_zero():
    assert eddington.calculate_eddington_number([]) == 0


def test_fifty_days_of_fifty_km_gives_e_at_least_fifty():
    # 50 distinct days, each a 50 km ride (50000 m) -> E(km) >= 50.
    acts = [_act(50000, f'2025-01-{d:02d}T08:00:00Z') for d in range(1, 29)]
    acts += [_act(50000, f'2025-02-{d:02d}T08:00:00Z') for d in range(1, 23)]
    assert len(acts) == 50
    assert eddington.calculate_eddington_number(acts, unit='km') == 50


def test_km_exceeds_miles_for_same_rides():
    # Same rides measured in km yield a larger E than in miles (km are shorter units).
    acts = [_act(60000, f'2025-03-{d:02d}T08:00:00Z') for d in range(1, 21)]
    e_km = eddington.calculate_eddington_number(acts, unit='km')
    e_miles = eddington.calculate_eddington_number(acts, unit='miles')
    assert e_km >= e_miles
    assert e_km == 20  # 20 days of 60 km each -> E(km) = 20


def test_non_cycling_types_are_excluded_by_default():
    # A pile of runs must not count toward the cycling Eddington.
    runs = [_act(50000, f'2025-04-{d:02d}T08:00:00Z', activity_type='Run')
            for d in range(1, 26)]
    assert eddington.calculate_eddington_number(runs, unit='km') == 0


def test_activity_type_key_is_what_the_filter_reads():
    # A raw Strava activity carries `type`, NOT `activity_type`; the engine filters
    # on `activity_type`, so an untransformed raw list silently yields E=0. This is
    # the transform-contract guard the compute path relies on.
    raw = [{'distance': 50000, 'start_date': f'2025-05-{d:02d}T08:00:00Z',
            'type': 'Ride'} for d in range(1, 26)]
    assert eddington.calculate_eddington_number(raw, unit='km') == 0
    # Once the `type` is mapped to `activity_type`, the same rides count.
    fixed = [dict(a, activity_type=a['type']) for a in raw]
    assert eddington.calculate_eddington_number(fixed, unit='km') == 25
