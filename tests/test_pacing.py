"""Unit tests for shared/pacing.py — the club-agnostic pacing engine.

These prove the extracted engine behaves the same for BrevetHub's km-through-the-
unit-agnostic-field usage as it does for Team Asha (which the unchanged
tests/test_custom_plan_service.py covers via the shim). Pure functions, no DB /
network, so they run anywhere.
"""
from shared.pacing import recalculate_cumulative_values, _get_cutoff_hours


def test_acp_cutoff_band_mapping():
    """The ACP distance -> control-cutoff bands the mission pins."""
    assert _get_cutoff_hours(200) == 13.5
    assert _get_cutoff_hours(300) == 20
    assert _get_cutoff_hours(400) == 27
    assert _get_cutoff_hours(600) == 40
    assert _get_cutoff_hours(1000) == 75
    # Below/at a band boundary maps to that band; beyond the mapped bands -> None.
    assert _get_cutoff_hours(150) == 13.5
    assert _get_cutoff_hours(1200) is None
    assert _get_cutoff_hours(None) is None
    assert _get_cutoff_hours(0) is None


def _km_stops(speed_kmh=20.0):
    """A 200 km brevet, one control every 100 km, paced at ``speed_kmh``.

    Kilometres go straight through the engine's unit-agnostic ``distance_miles``
    field (no conversion); segment time is derived from the target speed.
    """
    stops = []
    prev = 0.0
    for cum in (100.0, 200.0):
        seg = cum - prev
        stops.append({
            'distance_miles': cum,
            'elevation_gain': 0,
            'segment_time_min': int(round(seg / speed_kmh * 60)),
            'stop_duration_min': 0,
        })
        prev = cum
    return stops


def test_km_passthrough_yields_kmh_avg_speed():
    """20 km/h in -> avg_speed 20.0 km/h out (the unit-agnostic passthrough)."""
    out = recalculate_cumulative_values(
        _km_stops(20.0), {'name': ''}, cutoff_hours=13.5, total_mi=200)
    assert out[0]['avg_speed'] == 20.0
    assert out[1]['avg_speed'] == 20.0
    assert out[0]['seg_dist'] == 100
    assert out[1]['seg_dist'] == 100


def test_cumulative_and_arrival_minutes():
    """Cumulative/arrival time is the summed segment time (no rest stops)."""
    out = recalculate_cumulative_values(
        _km_stops(20.0), {'name': ''}, cutoff_hours=13.5, total_mi=200)
    # 100 km @ 20 km/h = 5 h = 300 min per segment.
    assert out[0]['arrival_time_min'] == 300
    assert out[1]['arrival_time_min'] == 600
    assert out[1]['cum_time_min'] == 600


def test_time_bank_vs_cutoff():
    """Time bank = bookend (fraction * cutoff) minus arrival, per stop."""
    out = recalculate_cumulative_values(
        _km_stops(20.0), {'name': ''}, cutoff_hours=13.5, total_mi=200)
    # Stop 1: fraction 0.5 * 13.5h*60 = 405 bookend; arrival 300 -> bank 105.
    assert out[0]['bookend_time_min'] == 405
    assert out[0]['time_bank_min'] == 105
    # Stop 2 (finish): fraction 1.0 * 810 = 810; arrival 600 -> bank 210.
    assert out[1]['bookend_time_min'] == 810
    assert out[1]['time_bank_min'] == 210


def test_faster_pace_grows_the_time_bank():
    """A faster target arrives earlier, so the time bank is larger."""
    slow = recalculate_cumulative_values(
        _km_stops(15.0), {'name': ''}, cutoff_hours=13.5, total_mi=200)
    fast = recalculate_cumulative_values(
        _km_stops(25.0), {'name': ''}, cutoff_hours=13.5, total_mi=200)
    assert fast[-1]['time_bank_min'] > slow[-1]['time_bank_min']
