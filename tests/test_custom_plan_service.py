"""Unit tests for services.custom_plan_service.recalculate_cumulative_values.

Focus: the time-bank (time_bank_min) regression where a custom plan whose NAME has no
distance class (e.g. "Mihir's Push pace") yielded cutoff_hours=None and therefore a
None time bank for every stop on the mobile custom-plan view (routes/live.py). The fix
lets the caller pass the canonical cutoff (ride.time_limit_hours) and the plan total.

These are pure-function tests (no DB / no network), so they run reliably anywhere.
"""

from services.custom_plan_service import recalculate_cumulative_values


def _stops():
    """A simple 2-stop plan: 60 mi then 120 mi, 4h riding per segment, no breaks."""
    return [
        {'distance_miles': 60, 'elevation_gain': 1200, 'segment_time_min': 240, 'stop_duration_min': 0},
        {'distance_miles': 120, 'elevation_gain': 2400, 'segment_time_min': 240, 'stop_duration_min': 0},
    ]


def test_name_without_distance_zeroes_bank_without_kwargs():
    """Repro: a custom plan name with no distance class -> None time bank (the bug)."""
    out = recalculate_cumulative_values(_stops(), {'name': "Mihir's Push pace"})
    assert all(s['time_bank_min'] is None for s in out)


def test_cutoff_and_total_mi_restore_bank():
    """Fix: passing the canonical cutoff + plan total computes the time bank regardless
    of the (distance-less) custom plan name."""
    out = recalculate_cumulative_values(
        _stops(), {'name': "Mihir's Push pace"}, cutoff_hours=40.0, total_mi=120)
    # 60/120 * 40h * 60 = 1200 bookend; arrival 240 -> bank 960. Then 1.0 -> 2400 - 480 = 1920.
    assert out[0]['time_bank_min'] == 960
    assert out[1]['time_bank_min'] == 1920


def test_fallback_name_with_distance_still_works():
    """Backward compatibility: with no kwargs, a name carrying a distance class (200K ->
    13.5h ACP cutoff) still derives the time bank as before."""
    out = recalculate_cumulative_values(_stops(), {'name': 'Davis 200K'})
    # 0.5 * 13.5h * 60 = 405 bookend; arrival 240 -> bank 165.
    assert out[0]['time_bank_min'] == 165
    assert out[1]['time_bank_min'] is not None


def test_total_mi_used_as_fraction_basis():
    """When total_mi exceeds the last stop's cumulative distance (e.g. the finish lies
    past the last listed control), the fraction basis is the plan total, not max(stop)."""
    out = recalculate_cumulative_values(
        _stops(), {'name': 'x'}, cutoff_hours=40.0, total_mi=180)
    # 60/180 * 2400 = 800 bookend; arrival 240 -> bank 560.
    assert out[0]['time_bank_min'] == 560


def test_cutoff_given_but_total_mi_none_falls_back_to_max_stop():
    """A real cutoff with no total_mi uses the largest cumulative stop distance (120) as
    the fraction basis — the same behavior as the legacy path."""
    out = recalculate_cumulative_values(_stops(), {'name': 'x'}, cutoff_hours=40.0)
    # basis = max stop = 120; 60/120 * 2400 = 1200 bookend; arrival 240 -> bank 960.
    assert out[0]['time_bank_min'] == 960


def test_parity_with_web_inline_formula():
    """Guard against drift: the service's time bank must equal the web custom views'
    inline recompute (routes/riders.py) for the same inputs — fraction of plan total
    times the cutoff minutes, minus arrival time."""
    cutoff_hours, total_mi = 40.0, 120
    out = recalculate_cumulative_values(
        _stops(), {'name': "Mihir's Push pace"}, cutoff_hours=cutoff_hours, total_mi=total_mi)
    for s in out:
        fraction = float(s['distance_miles']) / total_mi
        web_bookend = round(fraction * cutoff_hours * 60)
        web_bank = web_bookend - s['arrival_time_min']
        assert s['time_bank_min'] == web_bank
