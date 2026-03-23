"""Tests for weather service — route sampling, bearing math, headwind, Open-Meteo, caching, formatting."""
import math
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock


# ── Sample data ─────────────────────────────────────────────────────

SAMPLE_TRACK_POINTS = [
    {'y': 37.77, 'x': -122.41, 'd': 0, 'e': 10},
    {'y': 37.70, 'x': -122.30, 'd': 25000, 'e': 50},
    {'y': 37.60, 'x': -122.20, 'd': 50000, 'e': 100},
    {'y': 37.50, 'x': -122.10, 'd': 75000, 'e': 80},
    {'y': 37.40, 'x': -122.00, 'd': 100000, 'e': 150},
    {'y': 37.30, 'x': -121.90, 'd': 125000, 'e': 200},
    {'y': 37.20, 'x': -121.80, 'd': 150000, 'e': 50},
    {'y': 37.10, 'x': -121.70, 'd': 175000, 'e': 30},
    {'y': 37.00, 'x': -121.60, 'd': 200000, 'e': 20},
    {'y': 36.97, 'x': -121.55, 'd': 210000, 'e': 10},
]

SAMPLE_HOURLY_TIMES = [f"2026-03-17T{h:02d}:00" for h in range(24)]

SAMPLE_WEATHER_RESPONSE_SINGLE = {
    'hourly': {
        'time': SAMPLE_HOURLY_TIMES,
        'temperature_2m': [10.0] * 24,
        'wind_speed_10m': [15.0] * 24,
        'wind_direction_10m': [270] * 24,
        'precipitation_probability': [20] * 24,
        'weather_code': [3] * 24,
    }
}

SAMPLE_WEATHER_RESPONSE_MULTI = [
    {
        'hourly': {
            'time': SAMPLE_HOURLY_TIMES,
            'temperature_2m': [12.0] * 24,
            'wind_speed_10m': [20.0] * 24,
            'wind_direction_10m': [270] * 24,
            'precipitation_probability': [10] * 24,
            'weather_code': [0] * 24,
        }
    },
    {
        'hourly': {
            'time': SAMPLE_HOURLY_TIMES,
            'temperature_2m': [8.0] * 24,
            'wind_speed_10m': [25.0] * 24,
            'wind_direction_10m': [180] * 24,
            'precipitation_probability': [50] * 24,
            'weather_code': [63] * 24,
        }
    },
]


# ── WTHR-03: Route Sampling ─────────────────────────────────────────

class TestSampleTrackPoints:
    def test_empty_input_returns_empty(self):
        from services.weather import sample_track_points
        assert sample_track_points([]) == []

    def test_includes_first_and_last(self):
        from services.weather import sample_track_points
        result = sample_track_points(SAMPLE_TRACK_POINTS, interval_m=50000)
        # First point
        assert result[0]['lat'] == 37.77
        assert result[0]['lng'] == -122.41
        assert result[0]['distance_m'] == 0
        # Last point
        assert result[-1]['lat'] == 36.97
        assert result[-1]['lng'] == -121.55

    def test_spacing_at_50km(self):
        from services.weather import sample_track_points
        result = sample_track_points(SAMPLE_TRACK_POINTS, interval_m=50000)
        # 210km route with 50km intervals: first + 50k + 100k + 150k + 200k + last
        # Last might merge with 200k if gap < 10%
        assert len(result) >= 4
        assert len(result) <= 6

    def test_skips_none_lat_lng(self):
        from services.weather import sample_track_points
        points_with_none = [
            {'y': 37.77, 'x': -122.41, 'd': 0, 'e': 10},
            {'y': None, 'x': -122.30, 'd': 25000, 'e': 50},
            {'y': 37.60, 'x': None, 'd': 50000, 'e': 100},
            {'y': 37.00, 'x': -121.60, 'd': 200000, 'e': 20},
        ]
        result = sample_track_points(points_with_none, interval_m=50000)
        for pt in result:
            assert pt['lat'] is not None
            assert pt['lng'] is not None

    def test_single_point(self):
        from services.weather import sample_track_points
        result = sample_track_points([SAMPLE_TRACK_POINTS[0]], interval_m=50000)
        assert len(result) == 1
        assert result[0]['lat'] == 37.77


# ── WTHR-04: Bearing Calculation ─────────────────────────────────────

class TestCalculateBearing:
    def test_due_east(self):
        from services.weather import calculate_bearing
        b = calculate_bearing(37.77, -122.41, 37.77, -121.41)
        assert 85 < b < 95  # ~90 degrees

    def test_due_north(self):
        from services.weather import calculate_bearing
        b = calculate_bearing(37.77, -122.41, 38.77, -122.41)
        assert b < 5 or b > 355  # ~0 degrees

    def test_due_south(self):
        from services.weather import calculate_bearing
        b = calculate_bearing(37.77, -122.41, 36.77, -122.41)
        assert 175 < b < 185  # ~180 degrees

    def test_due_west(self):
        from services.weather import calculate_bearing
        b = calculate_bearing(37.77, -122.41, 37.77, -123.41)
        assert 265 < b < 275  # ~270 degrees

    def test_result_in_range(self):
        from services.weather import calculate_bearing
        b = calculate_bearing(37.77, -122.41, 36.00, -121.00)
        assert 0 <= b < 360


# ── WTHR-05: Headwind Component ──────────────────────────────────────

class TestHeadwindComponent:
    def test_pure_headwind(self):
        """West wind (270°), rider heading east (90°) = headwind."""
        from services.weather import headwind_component
        hw = headwind_component(20, 270, 90)
        assert hw > 18  # close to +20

    def test_pure_tailwind(self):
        """East wind (90°), rider heading east (90°) = tailwind."""
        from services.weather import headwind_component
        hw = headwind_component(20, 90, 90)
        assert hw < -18  # close to -20

    def test_pure_crosswind(self):
        """North wind (0°), rider heading east (90°) = crosswind ≈ 0."""
        from services.weather import headwind_component
        hw = headwind_component(20, 0, 90)
        assert abs(hw) < 2  # near zero

    def test_no_wind(self):
        from services.weather import headwind_component
        hw = headwind_component(0, 270, 90)
        assert hw == 0


# ── Wind Label ───────────────────────────────────────────────────────

class TestWindLabel:
    def test_strong_headwind(self):
        from services.weather import wind_label
        assert wind_label(20) == "strong headwind"

    def test_headwind(self):
        from services.weather import wind_label
        assert wind_label(10) == "headwind"

    def test_crosswind(self):
        from services.weather import wind_label
        assert wind_label(0) == "crosswind / light"

    def test_tailwind(self):
        from services.weather import wind_label
        assert wind_label(-10) == "tailwind"

    def test_strong_tailwind(self):
        from services.weather import wind_label
        assert wind_label(-20) == "strong tailwind"


# ── WMO Code ─────────────────────────────────────────────────────────

class TestWmoToText:
    def test_clear_sky(self):
        from services.weather import wmo_to_text
        assert wmo_to_text(0) == "clear sky"

    def test_rain(self):
        from services.weather import wmo_to_text
        assert wmo_to_text(63) == "rain"

    def test_unknown_code(self):
        from services.weather import wmo_to_text
        assert wmo_to_text(999) == "code 999"


# ── WTHR-07: Hour Index Selection ────────────────────────────────────

class TestGetHourIndex:
    def test_selects_correct_hour(self):
        from services.weather import get_hour_index
        dt = datetime(2026, 3, 17, 14, 30)
        idx = get_hour_index(SAMPLE_HOURLY_TIMES, dt)
        assert idx == 14  # T14:00

    def test_empty_times_returns_zero(self):
        from services.weather import get_hour_index
        assert get_hour_index([], datetime(2026, 3, 17, 14, 0)) == 0

    def test_clamps_to_last_index(self):
        from services.weather import get_hour_index
        # hour 25 doesn't exist, clamp to 23
        dt = datetime(2026, 3, 18, 1, 0)  # next day — beyond 24h forecast
        idx = get_hour_index(SAMPLE_HOURLY_TIMES, dt)
        assert idx == 23  # clamped to last


# ── WTHR-06: Open-Meteo Batch Fetch ─────────────────────────────────

class TestFetchRouteWeather:
    @patch('services.weather.requests.get')
    def test_sends_batch_request(self, mock_get):
        from services.weather import fetch_route_weather
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_WEATHER_RESPONSE_MULTI
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        sample_pts = [
            {'lat': 37.77, 'lng': -122.41, 'distance_m': 0},
            {'lat': 37.00, 'lng': -121.60, 'distance_m': 200000},
        ]
        result = fetch_route_weather(sample_pts)

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        params = call_kwargs[1].get('params') or call_kwargs.kwargs.get('params')
        # Should have comma-separated lat/lng
        assert '37.77' in str(params.get('latitude', ''))
        assert '37.0' in str(params.get('latitude', '')) or '37.00' in str(params.get('latitude', ''))
        assert result == SAMPLE_WEATHER_RESPONSE_MULTI

    @patch('services.weather.requests.get')
    def test_wraps_single_location_response(self, mock_get):
        from services.weather import fetch_route_weather
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_WEATHER_RESPONSE_SINGLE
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        sample_pts = [{'lat': 37.77, 'lng': -122.41, 'distance_m': 0}]
        result = fetch_route_weather(sample_pts)
        # Single-location dict should be wrapped in list
        assert isinstance(result, list)
        assert len(result) == 1

    @patch('services.weather.requests.get')
    def test_empty_points_returns_empty(self, mock_get):
        from services.weather import fetch_route_weather
        result = fetch_route_weather([])
        assert result == []
        mock_get.assert_not_called()

    @patch('services.weather.requests.get')
    def test_timeout_set(self, mock_get):
        from services.weather import fetch_route_weather
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_WEATHER_RESPONSE_MULTI
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        fetch_route_weather([{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}])
        call_kwargs = mock_get.call_args
        assert call_kwargs[1].get('timeout') == 15 or call_kwargs.kwargs.get('timeout') == 15


# ── WTHR-08: Caching ────────────────────────────────────────────────

class TestGetCachedRouteWeather:
    @patch('services.weather.fetch_route_weather')
    def test_cache_hit_returns_cached(self, mock_fetch):
        from services.weather import get_cached_route_weather
        mock_cache = MagicMock()
        cached_data = [SAMPLE_WEATHER_RESPONSE_SINGLE]
        mock_cache.get.return_value = cached_data

        result = get_cached_route_weather(
            'test-route', '2026031714',
            [{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}],
            cache=mock_cache,
        )
        assert result == cached_data
        mock_fetch.assert_not_called()

    @patch('services.weather.fetch_route_weather')
    def test_cache_miss_fetches_and_stores(self, mock_fetch):
        from services.weather import get_cached_route_weather
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        fetched = [SAMPLE_WEATHER_RESPONSE_SINGLE]
        mock_fetch.return_value = fetched

        result = get_cached_route_weather(
            'test-route', '2026031714',
            [{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}],
            cache=mock_cache,
        )
        assert result == fetched
        mock_cache.set.assert_called_once()
        set_args = mock_cache.set.call_args
        assert set_args[1].get('timeout') == 3600 or set_args[0][2] == 3600 if len(set_args[0]) > 2 else set_args[1].get('timeout') == 3600

    @patch('services.weather.fetch_route_weather')
    def test_cache_key_format(self, mock_fetch):
        from services.weather import get_cached_route_weather
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        mock_fetch.return_value = []

        get_cached_route_weather(
            'sfr-300k', '2026031714',
            [{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}],
            cache=mock_cache,
        )
        mock_cache.get.assert_called_with('weather:sfr-300k:2026031714')


# ── WTHR-09: Response Formatting ────────────────────────────────────

class TestFormatWeatherResponse:
    def test_produces_segments(self):
        from services.weather import format_weather_response, sample_track_points
        sample_pts = [
            {'lat': 37.77, 'lng': -122.41, 'distance_m': 0},
            {'lat': 37.00, 'lng': -121.60, 'distance_m': 200000},
        ]
        bearings = [135.0]  # SE bearing
        weather_data = SAMPLE_WEATHER_RESPONSE_MULTI
        start_dt = datetime(2026, 3, 17, 6, 0)

        result = format_weather_response(sample_pts, weather_data, bearings, start_dt)
        assert 'segments' in result
        assert 'overall_assessment' in result
        assert 'temp_range' in result
        assert 'attribution' in result
        assert len(result['segments']) > 0

    def test_segment_has_required_fields(self):
        from services.weather import format_weather_response
        sample_pts = [
            {'lat': 37.77, 'lng': -122.41, 'distance_m': 0},
            {'lat': 37.00, 'lng': -121.60, 'distance_m': 200000},
        ]
        bearings = [135.0]
        weather_data = SAMPLE_WEATHER_RESPONSE_MULTI
        start_dt = datetime(2026, 3, 17, 6, 0)

        result = format_weather_response(sample_pts, weather_data, bearings, start_dt)
        seg = result['segments'][0]
        required_fields = [
            'distance_km', 'temperature_c', 'wind_speed_kmh',
            'wind_direction_deg', 'headwind_kmh', 'wind_label',
            'precip_percent', 'conditions',
        ]
        for field in required_fields:
            assert field in seg, f"Missing field: {field}"

    def test_attribution_mentions_open_meteo(self):
        from services.weather import format_weather_response
        result = format_weather_response(
            [{'lat': 37.77, 'lng': -122.41, 'distance_m': 0}],
            [SAMPLE_WEATHER_RESPONSE_SINGLE],
            [],
            datetime(2026, 3, 17, 6, 0),
        )
        assert 'open-meteo' in result['attribution'].lower()


# ── Wind Constants ───────────────────────────────────────────────────

class TestWindConstants:
    def test_heavy_wind_max_value(self):
        from services.weather import HEAVY_WIND_MAX_KMH
        assert HEAVY_WIND_MAX_KMH == 30

    def test_heavy_wind_avg_headwind_value(self):
        from services.weather import HEAVY_WIND_AVG_HEADWIND_KMH
        assert HEAVY_WIND_AVG_HEADWIND_KMH == 15

    def test_constants_importable(self):
        from services.weather import HEAVY_WIND_MAX_KMH, HEAVY_WIND_AVG_HEADWIND_KMH
        assert HEAVY_WIND_MAX_KMH is not None
        assert HEAVY_WIND_AVG_HEADWIND_KMH is not None


# ── Crosswind Component ──────────────────────────────────────────────

class TestCrosswindComponent:
    def test_pure_crosswind(self):
        """North wind (from=0 deg), rider heading east (90 deg) -> full crosswind."""
        from services.weather import crosswind_component
        cw = crosswind_component(20, 0, 90)
        assert abs(cw) > 18  # close to full speed

    def test_pure_headwind_direction(self):
        """West wind (from=270 deg), rider heading east (90 deg) -> near zero crosswind."""
        from services.weather import crosswind_component
        cw = crosswind_component(20, 270, 90)
        assert abs(cw) < 2  # near zero

    def test_pure_tailwind_direction(self):
        """East wind (from=90 deg), rider heading east (90 deg) -> near zero crosswind."""
        from services.weather import crosswind_component
        cw = crosswind_component(20, 90, 90)
        assert abs(cw) < 2  # near zero

    def test_no_wind(self):
        from services.weather import crosswind_component
        cw = crosswind_component(0, 270, 90)
        assert cw == 0

    def test_45_degree_angle(self):
        """Wind at 45-degree offset -> crosswind approximately wind_speed * sin(45) ~= 14.1."""
        from services.weather import crosswind_component
        # Rider heading east (90 deg), wind from south-west (225 deg)
        # wind travel = (225+180)%360 = 45 deg
        # angle = 45 - 90 = -45 deg
        # sin(-45) = -0.707, so crosswind ~ -14.1
        cw = crosswind_component(20, 225, 90)
        assert abs(abs(cw) - 14.1) < 1.0  # ~14.1


# ── WTHR-10: Graceful Degradation ───────────────────────────────────

class TestGracefulDegradation:
    @patch('services.weather.requests.get')
    def test_api_timeout_raises(self, mock_get):
        """fetch_route_weather propagates RequestException on timeout."""
        import requests
        from services.weather import fetch_route_weather
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        with pytest.raises(requests.exceptions.RequestException):
            fetch_route_weather([{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}])

    def test_missing_hourly_keys_safe_defaults(self):
        from services.weather import format_weather_response
        # Weather data with missing keys
        incomplete_data = [{'hourly': {'time': SAMPLE_HOURLY_TIMES}}]
        result = format_weather_response(
            [{'lat': 37.77, 'lng': -122.41, 'distance_m': 0},
             {'lat': 37.00, 'lng': -121.60, 'distance_m': 200000}],
            incomplete_data,
            [90.0],
            datetime(2026, 3, 17, 6, 0),
        )
        # Should not crash, produce segments with defaults
        assert 'segments' in result
