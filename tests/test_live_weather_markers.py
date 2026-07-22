"""Unit tests for shared.weather.build_live_weather_markers — the along-route weather
markers (wind arrow + °F) rendered on the shared Radial live map. compute_stop_winds is
patched so these exercise ONLY the subsample / shape / fail-soft logic of the builder."""
from datetime import date
from unittest.mock import patch

from shared.weather import build_live_weather_markers


def _samples(n, step_m=5000):
    """n sample points every step_m metres, each with lat/lng/distance_m."""
    return [{'lat': 37.0 + i * 0.01, 'lng': -122.0 - i * 0.01, 'distance_m': i * step_m}
            for i in range(n)]


def test_empty_inputs_return_empty():
    assert build_live_weather_markers(None, None, date(2026, 7, 4), '06:00') == []
    assert build_live_weather_markers({'x': 1}, [], date(2026, 7, 4), '06:00') == []


def test_subsamples_by_interval_and_shapes_markers():
    # 40 samples * 5 km = ~124 mi of route; at a 15-mi interval we keep ~9 points.
    samples = _samples(40)
    # One wind dict per pseudo-stop the builder passes in.
    def fake_winds(stops, *a, **k):
        return [{'temperature_f': 68, 'wind_speed_mph': 12, 'wind_type': 'headwind',
                 'wind_direction_deg': 90} for _ in stops]
    with patch('shared.weather.compute_stop_winds', side_effect=fake_winds):
        out = build_live_weather_markers(samples, samples, date(2026, 7, 4), '06:00',
                                         interval_mi=15.0)
    assert 5 <= len(out) <= 12                     # subsampled, not one-per-5km-sample
    m = out[0]
    assert set(m) == {'lat', 'lng', 'temp_f', 'wind_speed_mph', 'wind_type', 'arrow_deg', 'color'}
    assert m['temp_f'] == 68 and m['wind_speed_mph'] == 12
    assert m['wind_type'] == 'headwind' and m['color'] == '#dc2626'   # headwind → red
    assert isinstance(m['arrow_deg'], (int, float))


def test_none_wind_entries_are_skipped():
    samples = _samples(30)
    def some_none(stops, *a, **k):
        return [None if i % 2 else {'temperature_f': 60, 'wind_speed_mph': 5,
                                    'wind_type': 'tailwind', 'wind_direction_deg': 270}
                for i in range(len(stops))]
    with patch('shared.weather.compute_stop_winds', side_effect=some_none):
        out = build_live_weather_markers(samples, samples, date(2026, 7, 4), '06:00')
    assert out and all(w['wind_type'] == 'tailwind' and w['color'] == '#16a34a' for w in out)


def test_fail_soft_on_error():
    samples = _samples(10)
    with patch('shared.weather.compute_stop_winds', side_effect=RuntimeError('boom')):
        assert build_live_weather_markers(samples, samples, date(2026, 7, 4), '06:00') == []
