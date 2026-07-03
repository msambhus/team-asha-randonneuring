"""Pure unit tests for services.segment_analysis.

No DB, no network. compute_gradient_band_baseline mocks the models query and
feeds hand-built zlib-compressed JSON streams.
"""

import json
import zlib
from unittest.mock import patch

from services.segment_analysis import (
    compute_gradient_band_baseline,
    build_segment_narratives,
    build_overall_narrative,
    _band_for_grade,
)


# ── helpers ─────────────────────────────────────────────────────────────

def _compress(streams):
    """Compress a streams dict the same way strava_analysis stores them."""
    return zlib.compress(json.dumps(streams).encode(), level=6)


def _planned_row(location, **kw):
    row = {
        'location': location,
        'stop_type': 'control',
        'distance_miles': kw.get('distance_miles', 20.0),
        'is_extra': False,
        'plan_speed_mph': kw.get('plan_speed_mph'),
        'actual_avg_watts': kw.get('actual_avg_watts'),
        'actual_np_watts': kw.get('actual_np_watts'),
        'actual_avg_cadence': kw.get('actual_avg_cadence'),
        'actual_avg_hr': kw.get('actual_avg_hr'),
        'actual_speed_mph': kw.get('actual_speed_mph'),
        'actual_elev_gain_ft': kw.get('actual_elev_gain_ft'),
        'actual_grade_pct': kw.get('actual_grade_pct'),
        'actual_segment_min': kw.get('actual_segment_min'),
        'vs_prev': kw.get('vs_prev'),
    }
    return row


# ── gradient band binning ───────────────────────────────────────────────

def test_band_for_grade_thresholds():
    assert _band_for_grade(-5) == 'descent'
    assert _band_for_grade(-1.01) == 'descent'
    assert _band_for_grade(-1) == 'flat'
    assert _band_for_grade(0) == 'flat'
    assert _band_for_grade(1) == 'flat'
    assert _band_for_grade(1.01) == 'rolling'
    assert _band_for_grade(4) == 'rolling'
    assert _band_for_grade(4.01) == 'climb'
    assert _band_for_grade(10) == 'climb'
    assert _band_for_grade(None) is None


def test_compute_gradient_band_baseline_bins_correctly():
    # 2 flat samples, 2 climb samples. velocity in m/s.
    streams = {
        'grade_smooth':     [0.0,  0.5,   6.0,   8.0],
        'watts':            [100,  120,   250,   270],
        'velocity_smooth':  [8.0,  8.0,   2.0,   2.0],   # m/s
        'cadence':          [90,   92,    70,    72],
    }
    rows = [{'ride_id': 1, 'activity_streams': _compress(streams)}]

    with patch('models.get_rider_rides_with_cached_streams', return_value=rows):
        result = compute_gradient_band_baseline(rider_id=42)

    assert set(result.keys()) == {'flat', 'climb'}

    flat = result['flat']
    assert flat['n_samples'] == 2
    assert flat['avg_watts'] == 110               # (100+120)/2
    assert flat['avg_cadence'] == 91              # (90+92)/2
    # (8.0+8.0)/2 m/s * 2.23694 = 17.9 mph
    assert flat['avg_speed_mph'] == round(8.0 * 2.23694, 1)

    climb = result['climb']
    assert climb['n_samples'] == 2
    assert climb['avg_watts'] == 260              # (250+270)/2
    assert climb['avg_speed_mph'] == round(2.0 * 2.23694, 1)


def test_compute_gradient_band_baseline_excludes_ride_and_caps():
    good = {
        'grade_smooth':    [0.0, 0.0],
        'watts':           [150, 150],
        'velocity_smooth': [7.0, 7.0],
        'cadence':         [88, 88],
    }
    rows = [
        {'ride_id': 99, 'activity_streams': _compress(good)},   # excluded
        {'ride_id': 1, 'activity_streams': _compress(good)},
    ]
    with patch('models.get_rider_rides_with_cached_streams', return_value=rows):
        result = compute_gradient_band_baseline(rider_id=42, exclude_ride_id=99)
    assert result['flat']['n_samples'] == 2  # only ride 1 counted


def test_compute_gradient_band_baseline_handles_no_data():
    with patch('models.get_rider_rides_with_cached_streams', return_value=[]):
        assert compute_gradient_band_baseline(rider_id=42) == {}


def test_compute_gradient_band_baseline_guards_mismatched_streams():
    # grade has 3 samples but every metric stream is a different length →
    # no metric is index-aligned → ride skipped → {}.
    streams = {
        'grade_smooth':    [0.0, 5.0, 6.0],
        'watts':           [100, 120],          # len 2 != 3
        'velocity_smooth': [8.0],               # len 1 != 3
        'cadence':         [90, 92, 70, 71],    # len 4 != 3
    }
    rows = [{'ride_id': 1, 'activity_streams': _compress(streams)}]
    with patch('models.get_rider_rides_with_cached_streams', return_value=rows):
        assert compute_gradient_band_baseline(rider_id=42) == {}


def test_compute_gradient_band_baseline_survives_bad_blob():
    rows = [{'ride_id': 1, 'activity_streams': b'not-zlib'}]
    with patch('models.get_rider_rides_with_cached_streams', return_value=rows):
        assert compute_gradient_band_baseline(rider_id=42) == {}


# ── segment narratives ──────────────────────────────────────────────────

def test_narrative_power_drop_vs_prev():
    rows = [_planned_row(
        'Checkpoint A',
        actual_avg_watts=140,
        actual_grade_pct=0.5,
        vs_prev={'watts_pct': -20},
    )]
    out = build_segment_narratives(rows)
    assert 'Checkpoint A' in out
    text = out['Checkpoint A']
    assert '140 W' in text
    assert '20% lower' in text


def test_narrative_power_higher_vs_prev():
    rows = [_planned_row(
        'Checkpoint B',
        actual_avg_watts=200,
        actual_grade_pct=0.0,
        vs_prev={'watts_pct': 15},
    )]
    out = build_segment_narratives(rows)
    assert '15% higher' in out['Checkpoint B']


def test_narrative_flat_headwind_slowdown():
    rows = [_planned_row(
        'Windy Flats',
        actual_avg_watts=160,
        actual_grade_pct=0.2,            # near-flat, abs < 1.5
        actual_speed_mph=14.0,
        vs_prev={'speed_pct': -12},      # slowed down
    )]
    stop_wind = {
        'Windy Flats': {
            'wind_speed_mph': 12.0,
            'wind_type': 'headwind',
            'headwind_kmh': 20.0,
            'crosswind_kmh': 2.0,
            'temperature_c': 15.0,
            'wind_arrow_deg': 180,
        }
    }
    out = build_segment_narratives(rows, stop_wind=stop_wind)
    text = out['Windy Flats']
    assert 'dropped 12% on flat ground' in text
    # 20 km/h * 0.621371 = 12.4 mph
    assert f'{round(20.0 * 0.621371, 1)} mph headwind' in text


def test_narrative_no_headwind_sentence_when_crosswind():
    rows = [_planned_row(
        'Cross Flats',
        actual_avg_watts=160,
        actual_grade_pct=0.2,
        vs_prev={'speed_pct': -12},
    )]
    stop_wind = {'Cross Flats': {'wind_type': 'crosswind', 'headwind_kmh': 0.0}}
    out = build_segment_narratives(rows, stop_wind=stop_wind)
    text = out.get('Cross Flats', '')
    assert 'headwind' not in text


def test_narrative_climb():
    rows = [_planned_row(
        'Big Hill',
        actual_avg_watts=240,
        actual_grade_pct=6.5,
        actual_elev_gain_ft=850,
        vs_prev={'watts_pct': 30, 'speed_pct': -25},
    )]
    out = build_segment_narratives(rows)
    text = out['Big Hill']
    assert 'climb (6.5%, 850 ft' in text
    assert 'more power but moved slower' in text


def test_narrative_cadence_drop():
    rows = [_planned_row(
        'Grind',
        actual_avg_watts=180,
        actual_avg_cadence=68,
        actual_grade_pct=2.0,
        vs_prev={'cadence_pct': -12},
    )]
    out = build_segment_narratives(rows)
    assert 'Cadence dropped to 68 rpm' in out['Grind']


def test_narrative_vs_historical_band():
    rows = [_planned_row(
        'Climb Zone',
        actual_avg_watts=180,
        actual_grade_pct=7.0,
    )]
    band_baseline = {'climb': {'avg_watts': 240, 'avg_speed_mph': 5.0,
                               'avg_cadence': 70, 'n_samples': 500}}
    out = build_segment_narratives(rows, band_baseline=band_baseline)
    text = out['Climb Zone']
    # (180-240)/240 = -25%
    assert '25% below your usual power on climbs' in text


def test_narrative_missing_data_skipped():
    # No actuals, no vs_prev → nothing to say → location absent from result.
    rows = [_planned_row('Empty Stop')]
    out = build_segment_narratives(rows)
    assert 'Empty Stop' not in out
    assert out == {}


def test_narrative_skips_extra_rows():
    rows = [
        {'location': 'Unplanned @ 30mi', 'is_extra': True,
         'actual_avg_watts': 150, 'vs_prev': {'watts_pct': -10}},
    ]
    assert build_segment_narratives(rows) == {}


def test_narrative_caps_at_three_sentences():
    rows = [_planned_row(
        'Loaded',
        actual_avg_watts=180,
        actual_avg_cadence=65,
        actual_grade_pct=6.0,
        actual_elev_gain_ft=500,
        vs_prev={'watts_pct': 30, 'speed_pct': -20, 'cadence_pct': -15},
    )]
    band_baseline = {'climb': {'avg_watts': 240}}
    out = build_segment_narratives(rows, band_baseline=band_baseline)
    text = out['Loaded']
    # At most 3 sentences. Count sentence-ending periods (those followed by a
    # space or end-of-string), not decimal points like "6.0%".
    import re
    sentence_ends = len(re.findall(r'\.(?:\s|$)', text))
    assert sentence_ends <= 3
    assert sentence_ends >= 1


# ── overall narrative ───────────────────────────────────────────────────

def test_overall_pace_faster_than_plan():
    summary = {'speed_delta_mph': 1.2, 'actual_avg_speed_mph': 15.0}
    out = build_overall_narrative(summary)
    assert any('1.2 mph faster than your plan' in s for s in out)


def test_overall_pace_slower_than_plan():
    summary = {'speed_delta_mph': -0.8, 'actual_avg_speed_mph': 12.0}
    out = build_overall_narrative(summary)
    assert any('0.8 mph slower than your plan' in s for s in out)


def test_overall_pace_vs_historical_baseline():
    summary = {'actual_avg_speed_mph': 16.0}
    baseline = {'avg_speed_mph': 14.0}
    out = build_overall_narrative(summary, ride_baseline=baseline)
    assert any('2.0 mph above your typical long-ride pace' in s for s in out)


def test_overall_stopped_time_over_plan():
    summary = {'actual_stopped_time_min': 95, 'break_delta_min': 40}
    out = build_overall_narrative(summary)
    assert any('95 min stopped' in s and '40 min more than planned' in s
               for s in out)


def test_overall_np_intensity_vs_baseline():
    summary = {}
    hr_power = {'weighted_avg_watts': 210}
    baseline = {'avg_np_watts': 180}
    out = build_overall_narrative(summary, hr_power=hr_power, ride_baseline=baseline)
    # (210-180)/180 = 17%
    assert any('17% above your usual' in s for s in out)


def test_overall_empty_when_nothing_meaningful():
    summary = {'speed_delta_mph': 0.1}   # below the 0.3 threshold
    assert build_overall_narrative(summary) == []
