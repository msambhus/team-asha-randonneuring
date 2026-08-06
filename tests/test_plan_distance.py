"""Canonical plan distance must not depend on a marketing/display name."""

from routes.riders import _plan_distance_km


def test_plan_distance_prefers_persisted_distance():
    assert _plan_distance_km({
        'name': 'Coulee Challenge',
        'distance_km': 1200,
        'total_distance_miles': 749.6,
    }) == 1200


def test_plan_distance_falls_back_to_measured_route_length():
    assert _plan_distance_km({
        'name': 'Unnamed grand brevet',
        'total_distance_miles': 749.6,
    }) == 1200


def test_plan_distance_keeps_legacy_name_fallback():
    assert _plan_distance_km({
        'name': 'SCR 600K',
        'total_distance_miles': 380,
    }) == 600
