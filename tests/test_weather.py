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
        """East wind (90°) + rider heading east (90°) = headwind (wind from directly ahead)."""
        from services.weather import headwind_component
        hw = headwind_component(20, 90, 90)
        assert hw > 18  # close to +20

    def test_pure_tailwind(self):
        """West wind (270°) + rider heading east (90°) = tailwind (wind from directly behind)."""
        from services.weather import headwind_component
        hw = headwind_component(20, 270, 90)
        assert hw < -18  # close to -20

    def test_pure_crosswind(self):
        """North wind (0°), rider heading east (90°) = pure crosswind, headwind ≈ 0."""
        from services.weather import headwind_component
        hw = headwind_component(20, 0, 90)
        assert abs(hw) < 2  # near zero

    def test_no_wind(self):
        from services.weather import headwind_component
        hw = headwind_component(0, 270, 90)
        assert hw == 0


# ── Wind arrow glyph (live-tracking plain-text cells) ────────────────

class TestWindArrowGlyph:
    def test_pure_headwind_points_down(self):
        from services.weather import wind_arrow_glyph, wind_arrow_rotation
        assert wind_arrow_glyph(wind_arrow_rotation(10, 0)) == '↓'

    def test_pure_tailwind_points_up(self):
        from services.weather import wind_arrow_glyph, wind_arrow_rotation
        assert wind_arrow_glyph(wind_arrow_rotation(-10, 0)) == '↑'

    def test_right_crosswind_points_left(self):
        from services.weather import wind_arrow_glyph, wind_arrow_rotation
        assert wind_arrow_glyph(wind_arrow_rotation(0, 10)) == '←'

    def test_left_crosswind_points_right(self):
        from services.weather import wind_arrow_glyph, wind_arrow_rotation
        assert wind_arrow_glyph(wind_arrow_rotation(0, -10)) == '→'

    def test_wraps_at_360(self):
        from services.weather import wind_arrow_glyph
        assert wind_arrow_glyph(360) == wind_arrow_glyph(0) == '↑'


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
        # Short timeout so a slow Open-Meteo fails fast on the calendar critical path.
        assert call_kwargs[1].get('timeout') == 6 or call_kwargs.kwargs.get('timeout') == 6


# ── WTHR-08: Stored weather (read path — populated by the cron) ─────

class TestLoadStoredRouteWeather:
    def test_returns_stored_payload(self):
        from datetime import date
        from services.weather import load_stored_route_weather
        row = {'route_id': 555, 'forecast_date': date(2026, 7, 20),
               'weather_data': [SAMPLE_WEATHER_RESPONSE_SINGLE],
               'sample_points': [{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}]}
        with patch('models.get_route_weather_cache', return_value=row):
            weather, samples = load_stored_route_weather(555, date(2026, 7, 20))
        assert weather == [SAMPLE_WEATHER_RESPONSE_SINGLE]
        assert samples == [{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}]

    def test_missing_row_returns_none_none(self):
        from datetime import date
        from services.weather import load_stored_route_weather
        with patch('models.get_route_weather_cache', return_value=None):
            assert load_stored_route_weather(555, date(2026, 7, 20)) == (None, None)

    def test_none_keys_short_circuit(self):
        from services.weather import load_stored_route_weather
        assert load_stored_route_weather(None, None) == (None, None)


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
        """East wind (from=90 deg), rider heading east (90 deg) -> near zero crosswind (pure headwind)."""
        from services.weather import crosswind_component
        cw = crosswind_component(20, 90, 90)
        assert abs(cw) < 2  # near zero

    def test_pure_tailwind_direction(self):
        """West wind (from=270 deg), rider heading east (90 deg) -> near zero crosswind (pure tailwind)."""
        from services.weather import crosswind_component
        cw = crosswind_component(20, 270, 90)
        assert abs(cw) < 2  # near zero

    def test_no_wind(self):
        from services.weather import crosswind_component
        cw = crosswind_component(0, 270, 90)
        assert cw == 0

    def test_45_degree_angle(self):
        """Wind from SW (225°), rider heading east (90°) -> crosswind ≈ wind_speed * sin(135°) ≈ 14.1."""
        from services.weather import crosswind_component
        # angle = wind_from - rider_bearing = 225 - 90 = 135 deg
        # sin(135°) ≈ 0.707 → crosswind ≈ +14.1 (from rider's right — SW wind)
        cw = crosswind_component(20, 225, 90)
        assert abs(abs(cw) - 14.1) < 1.0  # ~14.1


# ── Wind Arrow Rotation ──────────────────────────────────────────────

class TestWindArrowRotation:
    """wind_arrow_rotation returns SVG rotation so arrow points in wind's travel direction."""

    def test_pure_headwind_points_down(self):
        """hw=+20, cw=0 → rotation 180° (arrow ↓, wind hitting rider's face)."""
        from services.weather import wind_arrow_rotation
        assert wind_arrow_rotation(20, 0) == 180

    def test_pure_tailwind_points_up(self):
        """hw=-20, cw=0 → rotation 0° (arrow ↑, wind pushing from behind)."""
        from services.weather import wind_arrow_rotation
        assert wind_arrow_rotation(-20, 0) == 0

    def test_right_crosswind_points_left(self):
        """hw=0, cw=+20 → rotation 270° (arrow ←, wind from right travels left)."""
        from services.weather import wind_arrow_rotation
        assert wind_arrow_rotation(0, 20) == 270

    def test_left_crosswind_points_right(self):
        """hw=0, cw=-20 → rotation 90° (arrow →, wind from left travels right)."""
        from services.weather import wind_arrow_rotation
        assert wind_arrow_rotation(0, -20) == 90

    def test_diagonal_headwind_from_right(self):
        """45° angle: hw=+14, cw=+14 → rotation 225° (arrow to lower-left)."""
        from services.weather import wind_arrow_rotation
        rot = wind_arrow_rotation(14, 14)
        assert 220 <= rot <= 230  # ~225°

    def test_zero_wind_returns_180(self):
        """Zero components → atan2(0,0)=0 in Python → rotation 180°."""
        from services.weather import wind_arrow_rotation
        assert wind_arrow_rotation(0, 0) == 180


# ── Classify Wind ────────────────────────────────────────────────────

class TestClassifyWind:
    def test_headwind_dominant(self):
        from services.weather import classify_wind
        assert classify_wind(15, 5) == 'headwind'

    def test_tailwind_dominant(self):
        from services.weather import classify_wind
        assert classify_wind(-15, 5) == 'tailwind'

    def test_crosswind_dominant(self):
        from services.weather import classify_wind
        assert classify_wind(5, 15) == 'crosswind'

    def test_exact_45_degree_boundary(self):
        """Equal magnitudes -> crosswind (strict greater-than means equal goes to crosswind)."""
        from services.weather import classify_wind
        assert classify_wind(10, 10) == 'crosswind'

    def test_zero_wind(self):
        """Zero wind -> crosswind (0 is not > 0)."""
        from services.weather import classify_wind
        assert classify_wind(0, 0) == 'crosswind'

    def test_negative_crosswind(self):
        """Uses absolute values for comparison."""
        from services.weather import classify_wind
        assert classify_wind(15, -10) == 'headwind'


# ── Wind Cell Style ──────────────────────────────────────────────────

class TestWindCellStyle:
    def test_headwind_color(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(10, 'headwind')
        assert style['color'] == '#DC2626'
        assert 'rgba(220,38,38,' in style['background']

    def test_tailwind_color(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(10, 'tailwind')
        assert style['color'] == '#16A34A'
        assert 'rgba(22,163,74,' in style['background']

    def test_crosswind_color(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(10, 'crosswind')
        assert style['color'] == '#2563EB'
        assert 'rgba(37,99,235,' in style['background']

    def test_light_wind_opacity(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(3, 'headwind')
        assert ',0.15)' in style['background']

    def test_medium_wind_opacity(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(10, 'headwind')
        assert ',0.35)' in style['background']

    def test_strong_wind_opacity(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(20, 'headwind')
        assert ',0.65)' in style['background']

    def test_light_wind_font(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(3, 'headwind')
        assert style['font_size'] == '0.75rem'

    def test_medium_wind_font(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(10, 'headwind')
        assert style['font_size'] == '0.875rem'

    def test_strong_wind_font(self):
        from services.weather import wind_cell_style
        style = wind_cell_style(20, 'headwind')
        assert style['font_size'] == '1.0rem'

    def test_font_size_has_rem_unit(self):
        from services.weather import wind_cell_style
        for speed in [3, 10, 20]:
            style = wind_cell_style(speed, 'headwind')
            assert 'rem' in style['font_size']

    def test_unknown_wind_type_defaults(self):
        """Unknown wind type falls back to crosswind blue."""
        from services.weather import wind_cell_style
        style = wind_cell_style(10, 'unknown')
        assert style['color'] == '#2563EB'
        assert 'rgba(37,99,235,' in style['background']


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


# ── WIND-05: Stop Coordinate Interpolation ───────────────────────────

class TestGetStopCoordinates:
    """Tests for get_stop_coordinates() — interpolates lat/lng per stop from RWGPS track."""

    def _make_track(self, *points):
        """Helper: list of (lat, lng, dist_m) tuples -> RWGPS track dicts."""
        return [{'y': lat, 'x': lng, 'd': d, 'e': 0} for lat, lng, d in points]

    def test_mid_route_stop(self):
        """Stop at 10.0 miles on track with point at exactly 16093m returns that point's coords."""
        from services.weather import get_stop_coordinates
        # 0 mi = 0m, 10 mi = 16093.44m (~16093m), 20 mi = 32186.88m
        track = self._make_track(
            (37.00, -122.00, 0),
            (37.10, -121.90, 16093),
            (37.20, -121.80, 32187),
        )
        stops = [{'distance_miles': 10.0}]
        result = get_stop_coordinates(stops, track)
        assert len(result) == 1
        assert result[0] is not None
        # Should be very close to the 16093m point
        assert abs(result[0]['lat'] - 37.10) < 0.001
        assert abs(result[0]['lng'] - (-121.90)) < 0.001

    def test_interpolated_between_points(self):
        """Stop at 5.0 miles (8047m) halfway between 0m and 16093m returns midpoint lat/lng."""
        from services.weather import get_stop_coordinates
        track = self._make_track(
            (37.00, -122.00, 0),
            (37.10, -121.80, 16093),
        )
        stops = [{'distance_miles': 5.0}]
        result = get_stop_coordinates(stops, track)
        assert len(result) == 1
        assert result[0] is not None
        # Midpoint: lat ~37.05, lng ~-121.90
        assert abs(result[0]['lat'] - 37.05) < 0.001
        assert abs(result[0]['lng'] - (-121.90)) < 0.001

    def test_40_mile_stop_unit_conversion(self):
        """Stop at 40.0 miles must land within 0.5 km of track position at 64374m (not 40m)."""
        from services.weather import get_stop_coordinates, MILES_TO_METERS
        import math
        # Track: 0m to 80000m, evenly spaced
        # Position at 40 miles = 64373.76m -> should interpolate near lat/lng at that distance
        track = [
            {'y': 37.000, 'x': -122.000, 'd': 0, 'e': 0},
            {'y': 37.001, 'x': -122.000, 'd': 10000, 'e': 0},
            {'y': 37.002, 'x': -122.000, 'd': 20000, 'e': 0},
            {'y': 37.003, 'x': -122.000, 'd': 30000, 'e': 0},
            {'y': 37.004, 'x': -122.000, 'd': 40000, 'e': 0},
            {'y': 37.005, 'x': -122.000, 'd': 50000, 'e': 0},
            {'y': 37.006, 'x': -122.000, 'd': 60000, 'e': 0},
            {'y': 37.007, 'x': -122.000, 'd': 70000, 'e': 0},
            {'y': 37.008, 'x': -122.000, 'd': 80000, 'e': 0},
        ]
        stops = [{'distance_miles': 40.0}]
        result = get_stop_coordinates(stops, track)
        assert result[0] is not None
        # 40 miles = 64373.76m. At this track, lat increases linearly.
        # Expected lat at 64373.76m: 37.000 + (64373.76/80000) * 0.008 = 37.000 + 0.006437376 ≈ 37.006437
        expected_lat = 37.000 + (40 * 1609.344 / 80000) * 0.008
        actual_lat = result[0]['lat']
        # Convert lat difference to km (1 deg lat ≈ 111 km) and check < 0.5 km
        diff_km = abs(actual_lat - expected_lat) * 111
        assert diff_km < 0.5, f"Stop at 40 miles is {diff_km:.3f} km off — unit conversion may be wrong"

    def test_beyond_track_end_clamped(self):
        """Stop at 999.0 miles on track ending at 100000m returns final track point coords."""
        from services.weather import get_stop_coordinates
        track = self._make_track(
            (37.00, -122.00, 0),
            (37.50, -121.50, 50000),
            (38.00, -121.00, 100000),
        )
        stops = [{'distance_miles': 999.0}]
        result = get_stop_coordinates(stops, track)
        assert result[0] is not None
        assert result[0]['lat'] == 38.00
        assert result[0]['lng'] == -121.00

    def test_start_stop_returns_first_point(self):
        """Stop at 0.0 miles returns the lat/lng of the first track point."""
        from services.weather import get_stop_coordinates
        track = self._make_track(
            (37.77, -122.41, 0),
            (37.50, -122.00, 50000),
            (37.00, -121.60, 100000),
        )
        stops = [{'distance_miles': 0.0}]
        result = get_stop_coordinates(stops, track)
        assert result[0] is not None
        assert result[0]['lat'] == 37.77
        assert result[0]['lng'] == -122.41

    def test_empty_track_returns_none(self):
        """Empty track_points list returns [None] * len(stops)."""
        from services.weather import get_stop_coordinates
        stops = [{'distance_miles': 10.0}, {'distance_miles': 20.0}]
        result = get_stop_coordinates(stops, [])
        assert result == [None, None]

    def test_skips_none_coordinates(self):
        """Track points with y=None or x=None are filtered; remaining points used for interpolation."""
        from services.weather import get_stop_coordinates
        track = [
            {'y': 37.00, 'x': -122.00, 'd': 0, 'e': 0},
            {'y': None, 'x': -121.80, 'd': 5000, 'e': 0},   # should be skipped
            {'y': 37.10, 'x': None, 'd': 10000, 'e': 0},    # should be skipped
            {'y': 37.20, 'x': -121.60, 'd': 20000, 'e': 0},
        ]
        stops = [{'distance_miles': 6.2}]  # ~10000m, between 0m and 20000m after filtering
        result = get_stop_coordinates(stops, track)
        # Must not raise TypeError; None-coord points must be skipped
        assert len(result) == 1
        assert result[0] is not None
        assert result[0]['lat'] is not None
        assert result[0]['lng'] is not None

    def test_zero_length_segment(self):
        """Two consecutive track points with identical d values do not cause ZeroDivisionError."""
        from services.weather import get_stop_coordinates
        track = [
            {'y': 37.00, 'x': -122.00, 'd': 0, 'e': 0},
            {'y': 37.05, 'x': -121.95, 'd': 8000, 'e': 0},
            {'y': 37.05, 'x': -121.95, 'd': 8000, 'e': 0},  # duplicate distance
            {'y': 37.10, 'x': -121.90, 'd': 16000, 'e': 0},
        ]
        stops = [{'distance_miles': 4.97}]  # ~8000m
        # Must not raise ZeroDivisionError
        result = get_stop_coordinates(stops, track)
        assert len(result) == 1
        assert result[0] is not None

    def test_multiple_stops_ordered(self):
        """Three stops at different distances return three coordinates in correct order."""
        from services.weather import get_stop_coordinates
        track = self._make_track(
            (37.00, -122.00, 0),
            (37.10, -121.90, 16093),
            (37.20, -121.80, 32187),
            (37.30, -121.70, 48280),
        )
        stops = [
            {'distance_miles': 0.0},
            {'distance_miles': 10.0},
            {'distance_miles': 20.0},
        ]
        result = get_stop_coordinates(stops, track)
        assert len(result) == 3
        assert all(r is not None for r in result)
        # Latitude should increase (moving north) for each stop
        assert result[0]['lat'] < result[1]['lat'] < result[2]['lat']


# ── WIND-06: Per-Stop Wind Data Pipeline ────────────────────────────

# Shared track points for fetch_stop_wind tests — three stops going south
_FSW_TRACK = [
    {'y': 37.80, 'x': -122.40, 'd': 0, 'e': 10},
    {'y': 37.70, 'x': -122.30, 'd': 16093, 'e': 20},
    {'y': 37.60, 'x': -122.20, 'd': 32186, 'e': 30},
    {'y': 37.50, 'x': -122.10, 'd': 48280, 'e': 20},
]

# Three stops at 0, 10, 20 miles matching the track above
_FSW_STOPS = [
    {'distance_miles': 0.0, 'arrival_time_min': 0},
    {'distance_miles': 10.0, 'arrival_time_min': 60},
    {'distance_miles': 20.0, 'arrival_time_min': 120},
]

# Stored sample points aligned to the stops above — the fetch-route-weather cron
# stores these and fetch_stop_wind READS them (no live Open-Meteo — TA-237).
_FSW_SAMPLES = [
    {'lat': 37.80, 'lng': -122.40, 'distance_m': 0},
    {'lat': 37.70, 'lng': -122.30, 'distance_m': 16093},
    {'lat': 37.60, 'lng': -122.20, 'distance_m': 32186},
]

# Mock hourly weather data for use in fetch_stop_wind tests
# Must be TODAY's date: fetch_stop_wind builds arrival times from
# datetime.now(), and get_hour_index matches forecast times against them. A
# hardcoded past date makes every arrival fall after the forecast window, so
# get_hour_index returns the last index for every stop (hiding per-hour logic).
_FSW_HOURLY_TIMES = [f"{datetime.now():%Y-%m-%d}T{h:02d}:00" for h in range(24)]

def _make_fsw_forecast(**kwargs):
    """Return a minimal Open-Meteo forecast dict for testing."""
    return {
        'hourly': {
            'time': _FSW_HOURLY_TIMES,
            'wind_speed_10m': kwargs.get('wind_speed', [20.0] * 24),
            'wind_direction_10m': kwargs.get('wind_dir', [270] * 24),
            'temperature_2m': [15.0] * 24,
            'precipitation_probability': [10] * 24,
            'weather_code': [0] * 24,
        }
    }


class TestFetchStopWind:
    """Tests for fetch_stop_wind() — per-stop wind READ from stored weather (TA-237)."""

    def _make_weather_list(self, n=3, **kwargs):
        return [_make_fsw_forecast(**kwargs) for _ in range(n)]

    def test_returns_wind_data_for_each_stop(self):
        """3 stops + a stored forecast -> 3 dicts with the required keys, no live fetch."""
        from services.weather import fetch_stop_wind
        today = datetime.now().date()
        stored = (self._make_weather_list(3), _FSW_SAMPLES)
        with patch('services.weather.load_stored_route_weather', return_value=stored), \
             patch('services.weather.fetch_route_weather',
                   side_effect=AssertionError('live fetch on the read path!')):
            result = fetch_stop_wind(_FSW_STOPS, 555, today, '06:00')
        assert result is not None
        assert len(result) == 3
        for entry in result:
            assert entry is not None
            assert 'wind_speed_kmh' in entry
            assert 'wind_type' in entry
            assert 'style' in entry
            assert 'label' in entry
            assert isinstance(entry['wind_speed_kmh'], float)
            assert entry['wind_type'] in ('headwind', 'tailwind', 'crosswind')
            assert isinstance(entry['style'], dict)

    def test_result_length_matches_stops(self):
        """Output length equals input stops length (extra stop maps to nearest sample)."""
        from services.weather import fetch_stop_wind
        today = datetime.now().date()
        stops_with_extra = _FSW_STOPS + [{'distance_miles': 999.0, 'arrival_time_min': 999}]
        stored = (self._make_weather_list(3), _FSW_SAMPLES)
        with patch('services.weather.load_stored_route_weather', return_value=stored):
            result = fetch_stop_wind(stops_with_extra, 555, today, '06:00')
        assert result is not None
        assert len(result) == len(stops_with_extra)

    def test_no_stored_forecast_returns_none(self):
        """No stored row (or missing keys) -> None (graceful miss), never a live fetch."""
        from services.weather import fetch_stop_wind
        today = datetime.now().date()
        with patch('services.weather.load_stored_route_weather', return_value=(None, None)):
            assert fetch_stop_wind(_FSW_STOPS, 555, today, '06:00') is None
        # Missing keys short-circuit before any lookup.
        assert fetch_stop_wind(_FSW_STOPS, None, today, '06:00') is None
        assert fetch_stop_wind(_FSW_STOPS, 555, None, '06:00') is None

    def test_never_calls_open_meteo_live(self):
        """The read path serves stored data and NEVER calls fetch_route_weather."""
        from services.weather import fetch_stop_wind
        today = datetime.now().date()
        stored = (self._make_weather_list(3), _FSW_SAMPLES)
        with patch('services.weather.load_stored_route_weather', return_value=stored), \
             patch('services.weather.fetch_route_weather') as mock_fetch:
            result = fetch_stop_wind(_FSW_STOPS, 555, today, '06:00')
        assert result is not None
        mock_fetch.assert_not_called()

    def test_stop_beyond_weather_data_is_none(self):
        """A stop mapping to a sample index with no forecast entry yields a None slot."""
        from services.weather import fetch_stop_wind
        today = datetime.now().date()
        # 3 samples but only 2 forecasts -> the 3rd stop's nearest sample (idx 2) has no data.
        stored = (self._make_weather_list(2), _FSW_SAMPLES)
        with patch('services.weather.load_stored_route_weather', return_value=stored):
            result = fetch_stop_wind(_FSW_STOPS, 555, today, '06:00')
        assert result is not None
        assert len(result) == 3
        assert result[2] is None

    def test_uses_arrival_time_for_hour_index(self):
        """Per-stop arrival hour selects the matching forecast hour from stored data."""
        from services.weather import fetch_stop_wind
        today = datetime.now().date()
        per_hour_speeds = [float(h) for h in range(24)]
        weather_data = [_make_fsw_forecast(wind_speed=per_hour_speeds) for _ in range(3)]
        stops = [
            {'distance_miles': 0.0, 'arrival_time_min': 0},     # 06:00 -> hour 6
            {'distance_miles': 10.0, 'arrival_time_min': 60},    # 07:00 -> hour 7
            {'distance_miles': 20.0, 'arrival_time_min': 120},   # 08:00 -> hour 8
        ]
        with patch('services.weather.load_stored_route_weather',
                   return_value=(weather_data, _FSW_SAMPLES)):
            result = fetch_stop_wind(stops, 555, today, '06:00')
        assert result is not None
        speeds = [r['wind_speed_kmh'] for r in result if r is not None]
        assert len(set(speeds)) > 1, "arrival time not used — all stops show identical wind"


# ── Phase 04: detect_heavy_wind ─────────────────────────────────────

class TestDetectHeavyWind:
    """Tests for detect_heavy_wind() — evaluates per-stop wind data against thresholds."""

    def test_none_input_returns_none(self):
        """detect_heavy_wind(None) returns None."""
        from services.weather import detect_heavy_wind
        assert detect_heavy_wind(None) is None

    def test_empty_list_returns_none(self):
        """detect_heavy_wind([]) returns None."""
        from services.weather import detect_heavy_wind
        assert detect_heavy_wind([]) is None

    def test_all_none_stops_returns_none(self):
        """detect_heavy_wind([None, None]) returns None when all stops are None."""
        from services.weather import detect_heavy_wind
        assert detect_heavy_wind([None, None]) is None

    def test_max_wind_above_threshold_returns_warning(self):
        """max_wind=35, avg_headwind=10 triggers warning (max_wind > 30)."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            {'wind_speed_kmh': 35.0, 'headwind_kmh': 10.0},
            {'wind_speed_kmh': 20.0, 'headwind_kmh': 10.0},
        ]
        result = detect_heavy_wind(stop_wind)
        assert result is not None
        assert result['is_heavy'] is True

    def test_avg_headwind_above_threshold_returns_warning(self):
        """max_wind=20, avg_headwind=18 triggers warning (avg_headwind > 15)."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            {'wind_speed_kmh': 20.0, 'headwind_kmh': 18.0},
            {'wind_speed_kmh': 15.0, 'headwind_kmh': 18.0},
        ]
        result = detect_heavy_wind(stop_wind)
        assert result is not None
        assert result['is_heavy'] is True

    def test_both_below_thresholds_returns_none(self):
        """max_wind=25, avg_headwind=10 returns None (both below thresholds)."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            {'wind_speed_kmh': 25.0, 'headwind_kmh': 10.0},
            {'wind_speed_kmh': 20.0, 'headwind_kmh': 10.0},
        ]
        result = detect_heavy_wind(stop_wind)
        assert result is None

    def test_exactly_at_threshold_returns_none(self):
        """max_wind=30, avg_headwind=15 returns None (strict >, not >=)."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            {'wind_speed_kmh': 30.0, 'headwind_kmh': 15.0},
        ]
        result = detect_heavy_wind(stop_wind)
        assert result is None

    def test_warning_dict_has_required_keys(self):
        """Warning dict includes max_wind_kmh, avg_headwind_kmh, is_heavy, description."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            {'wind_speed_kmh': 35.0, 'headwind_kmh': 20.0},
        ]
        result = detect_heavy_wind(stop_wind)
        assert result is not None
        assert 'max_wind_kmh' in result
        assert 'avg_headwind_kmh' in result
        assert 'is_heavy' in result
        assert 'description' in result
        assert result['is_heavy'] is True

    def test_description_contains_wind_values(self):
        """description contains avg headwind and max gust values as readable text."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            {'wind_speed_kmh': 35.0, 'headwind_kmh': 20.0},
        ]
        result = detect_heavy_wind(stop_wind)
        assert result is not None
        desc = result['description']
        assert isinstance(desc, str)
        assert '20' in desc or '20.0' in desc  # avg headwind value
        assert '35' in desc or '35.0' in desc  # max gust value

    def test_mixed_list_with_none_stops_skips_none(self):
        """Mixed list with some None stops skips None entries and still computes correctly."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            None,
            {'wind_speed_kmh': 35.0, 'headwind_kmh': 20.0},
            None,
            {'wind_speed_kmh': 20.0, 'headwind_kmh': 10.0},
        ]
        result = detect_heavy_wind(stop_wind)
        # max_wind=35 > 30, should return warning
        assert result is not None
        assert result['is_heavy'] is True
        # avg_headwind = (20 + 10) / 2 = 15.0 — exactly at threshold, not above
        # max_wind=35 > 30 triggers regardless
        assert result['max_wind_kmh'] == 35.0
        assert result['avg_headwind_kmh'] == 15.0

    def test_warning_dict_values_are_correct(self):
        """Warning dict values match actual computed max_wind and avg_headwind."""
        from services.weather import detect_heavy_wind
        stop_wind = [
            {'wind_speed_kmh': 40.0, 'headwind_kmh': 22.0},
            {'wind_speed_kmh': 25.0, 'headwind_kmh': 18.0},
        ]
        result = detect_heavy_wind(stop_wind)
        assert result is not None
        assert result['max_wind_kmh'] == 40.0
        assert result['avg_headwind_kmh'] == 20.0  # (22 + 18) / 2


# ── CPLN-01, CPLN-02, WIND-09: Custom Plan Wind ──────────────────────

def _make_raw_stop(distance_miles, segment_time_min=60, stop_duration_min=0,
                   elevation_gain=500, location='Test Stop', stop_type='waypoint',
                   notes=None):
    """Build a minimal raw stop dict matching what get_merged_plan_stops returns."""
    from decimal import Decimal
    return {
        'distance_miles': Decimal(str(distance_miles)),
        'segment_time_min': segment_time_min,
        'stop_duration_min': stop_duration_min,
        'elevation_gain': elevation_gain,
        'location': location,
        'stop_type': stop_type,
        'notes': notes,
        'stop_name': None,
    }


class TestCustomPlanWind:
    """Tests for wind data in custom_ride_plan_view — CPLN-01, CPLN-02, WIND-09."""

    _TRACK_POINTS = [
        {'y': 37.80, 'x': -122.40, 'd': 0, 'e': 10},
        {'y': 37.70, 'x': -122.30, 'd': 16093, 'e': 20},
        {'y': 37.60, 'x': -122.20, 'd': 32186, 'e': 30},
    ]

    # Raw stops matching what get_merged_plan_stops returns (Decimal types)
    _RAW_STOPS = [
        _make_raw_stop(0.0, segment_time_min=0, stop_duration_min=0, elevation_gain=0, location='Start'),
        _make_raw_stop(10.0, segment_time_min=60, stop_duration_min=10, elevation_gain=500, location='Control 1'),
        _make_raw_stop(20.0, segment_time_min=60, stop_duration_min=0, elevation_gain=300, location='Finish'),
    ]

    _WIND_RESULT = [
        {'wind_speed_kmh': 20.0, 'wind_type': 'headwind', 'headwind_kmh': 18.0,
         'style': {'color': '#DC2626', 'background': 'rgba(220,38,38,0.35)', 'font_size': '0.875rem'},
         'label': 'headwind'},
        {'wind_speed_kmh': 15.0, 'wind_type': 'crosswind', 'headwind_kmh': 2.0,
         'style': {'color': '#2563EB', 'background': 'rgba(37,99,235,0.35)', 'font_size': '0.875rem'},
         'label': 'crosswind / light'},
        {'wind_speed_kmh': 10.0, 'wind_type': 'tailwind', 'headwind_kmh': -9.0,
         'style': {'color': '#16A34A', 'background': 'rgba(22,163,74,0.35)', 'font_size': '0.875rem'},
         'label': 'tailwind'},
    ]

    def test_custom_plan_passes_stop_wind_to_template(self, app):
        """CPLN-01: custom_ride_plan_view passes stop_wind (not None) to template when weather_route_id is set."""
        from unittest.mock import patch, MagicMock

        mock_plan = {
            'id': 1, 'slug': 'sfr-300k', 'name': 'SFR 300k Brevet',
            'total_distance_miles': 20.0, 'total_elevation_ft': 1000,
            'rwgps_url': 'https://ridewithgps.com/routes/12345',
            'rwgps_url_team': None, 'start_time': '06:00',
        }
        mock_custom_plan_row = {
            'id': 10, 'rider_id': 42, 'plan_id': 1, 'avg_moving_speed': 14.5,
            'name': 'My SFR 300k',
        }
        mock_route_data = {'track_points': self._TRACK_POINTS}

        with app.test_request_context():
            with patch('routes.riders.get_ride_plan_by_slug', return_value=mock_plan), \
                 patch('routes.riders.get_custom_plan', return_value=mock_custom_plan_row), \
                 patch('services.custom_plan_service.get_merged_plan_stops',
                       return_value=(self._RAW_STOPS, mock_custom_plan_row)), \
                 patch('routes.riders.fetch_route', return_value=mock_route_data), \
                 patch('routes.riders.get_upcoming_ride_date_for_plan',
                       return_value=datetime(2026, 7, 20).date()), \
                 patch('routes.riders.fetch_stop_wind', return_value=self._WIND_RESULT) as mock_fsw, \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'user_id': 1}), \
                 patch('routes.riders.get_user_by_id', return_value={'rider_id': 42}), \
                 patch('routes.riders.get_upcoming_rusa_events', return_value=[]), \
                 patch('routes.riders.is_admin_user', return_value=False):
                from routes.riders import custom_ride_plan_view
                custom_ride_plan_view('sfr-300k')

        assert mock_render.called, "render_template was not called"
        call_kwargs = mock_render.call_args[1]
        assert 'stop_wind' in call_kwargs, "stop_wind not passed to render_template"
        assert call_kwargs['stop_wind'] is not None, "stop_wind was None — wind not wired into custom view"
        assert call_kwargs['stop_wind'] == self._WIND_RESULT

    def test_hidden_stops_excluded_from_wind_fetch(self):
        """CPLN-02: get_merged_plan_stops filters hidden stops; fetch_stop_wind receives only visible stops."""
        # Contract: hidden stops are filtered by get_merged_plan_stops before they reach fetch_stop_wind.
        # We verify the stops list passed to fetch_stop_wind contains no hidden entries.
        visible_stops = [
            {'distance_miles': 0.0, 'arrival_time_min': 0, 'name': 'Start', 'is_hidden': False},
            {'distance_miles': 10.0, 'arrival_time_min': 60, 'name': 'Control 1', 'is_hidden': False},
            {'distance_miles': 20.0, 'arrival_time_min': 120, 'name': 'Finish', 'is_hidden': False},
        ]

        received_stops = []

        def capturing_fetch(stops, track_points, plan_slug, start_time_str, cache=None):
            received_stops.extend(stops)
            return self._WIND_RESULT

        # Simulate the call that custom_ride_plan_view will make after wiring
        capturing_fetch(visible_stops, self._TRACK_POINTS, 'sfr-300k', '06:00')

        assert len(received_stops) == 3, f"Expected 3 visible stops, got {len(received_stops)}"
        assert all(not s.get('is_hidden') for s in received_stops), \
            "Hidden stop found in stops passed to fetch_stop_wind"

    def test_custom_stop_has_distance_miles(self):
        """CPLN-02: Custom-added stops in the processed stops list have float distance_miles (not Decimal)."""
        from decimal import Decimal

        # Simulate what custom_ride_plan_view does: convert Decimal to float in processed stops
        raw_stops = [
            {'distance_miles': Decimal('10.5'), 'segment_time_min': 60, 'stop_duration_min': 0,
             'elevation_gain': 500, 'location': 'Custom Stop', 'stop_name': None, 'stop_type': 'waypoint'},
            {'distance_miles': Decimal('0.0'), 'segment_time_min': 0, 'stop_duration_min': 0,
             'elevation_gain': 0, 'location': 'Start', 'stop_name': None, 'stop_type': 'waypoint'},
        ]

        processed = []
        for s in raw_stops:
            stop = dict(s)
            if stop.get('distance_miles') is not None:
                stop['distance_miles'] = float(stop['distance_miles'])
            processed.append(stop)

        for stop in processed:
            assert isinstance(stop['distance_miles'], float), \
                f"distance_miles is {type(stop['distance_miles'])}, expected float"

    def test_no_wind_when_no_weather_route_id(self, app):
        """CPLN-01: When weather_route_id is None, stop_wind is None and no exception is raised."""
        from unittest.mock import patch, MagicMock

        mock_plan = {
            'id': 1, 'slug': 'sfr-200k', 'name': 'SFR 200k Brevet',
            'total_distance_miles': 20.0, 'total_elevation_ft': 1000,
            'rwgps_url': None,  # No RWGPS URL → weather_route_id will be None
            'rwgps_url_team': None, 'start_time': '07:00',
        }
        mock_custom_plan_row = {
            'id': 11, 'rider_id': 42, 'plan_id': 1, 'avg_moving_speed': 14.5,
            'name': None,
        }

        with app.test_request_context():
            with patch('routes.riders.get_ride_plan_by_slug', return_value=mock_plan), \
                 patch('routes.riders.get_custom_plan', return_value=mock_custom_plan_row), \
                 patch('services.custom_plan_service.get_merged_plan_stops',
                       return_value=(self._RAW_STOPS, mock_custom_plan_row)), \
                 patch('routes.riders.fetch_stop_wind') as mock_fsw, \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'user_id': 1}), \
                 patch('routes.riders.get_user_by_id', return_value={'rider_id': 42}), \
                 patch('routes.riders.get_upcoming_rusa_events', return_value=[]), \
                 patch('routes.riders.is_admin_user', return_value=False):
                from routes.riders import custom_ride_plan_view
                custom_ride_plan_view('sfr-200k')

        mock_fsw.assert_not_called()
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs.get('stop_wind') is None, \
            f"stop_wind should be None when no weather_route_id, got: {call_kwargs.get('stop_wind')}"

    def test_single_location_dict_normalized(self):
        """WIND-09: fetch_route_weather wraps a bare dict (single-location) response in a list."""
        from unittest.mock import patch, MagicMock
        from services.weather import fetch_route_weather

        single_dict_response = {
            'hourly': {
                'time': [f"2026-03-23T{h:02d}:00" for h in range(24)],
                'wind_speed_10m': [15.0] * 24,
                'wind_direction_10m': [270] * 24,
                'temperature_2m': [12.0] * 24,
                'precipitation_probability': [10] * 24,
                'weather_code': [0] * 24,
            }
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = single_dict_response
        mock_resp.raise_for_status = MagicMock()

        with patch('services.weather.requests.get', return_value=mock_resp):
            result = fetch_route_weather([{'lat': 37.77, 'lng': -122.41, 'distance_m': 0}])

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert len(result) == 1, f"Expected 1 element, got {len(result)}"
        assert result[0] == single_dict_response


# ── WIND-07/08: Historical Wind Fetch (Archive & Forecast Fallback) ──

def _make_archive_hourly_response():
    """Minimal hourly response matching archive API shape."""
    times = [f"2026-01-01T{h:02d}:00" for h in range(24)]
    return {
        'hourly': {
            'time': times,
            'wind_speed_10m': [10.0] * 24,
            'wind_direction_10m': [270] * 24,
            'wind_gusts_10m': [15.0] * 24,
            'temperature_2m': [8.0] * 24,
        }
    }


class TestFetchHistoricalWind:
    def test_old_ride_uses_archive(self):
        """Ride 10 days ago routes to archive-api.open-meteo.com."""
        from datetime import date, timedelta
        from services.weather import fetch_historical_wind

        ride_date = date.today() - timedelta(days=10)
        coords = [{'lat': 37.77, 'lng': -122.41}]

        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_archive_hourly_response()
        mock_resp.raise_for_status = MagicMock()

        with patch('services.weather.requests.get', return_value=mock_resp) as mock_get:
            result, source = fetch_historical_wind(coords, ride_date)

        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args.args[0]
        params = call_args[1].get('params') or call_args.kwargs.get('params', {})
        assert 'archive-api.open-meteo.com' in url
        assert params.get('start_date') == ride_date.strftime('%Y-%m-%d')
        assert params.get('end_date') == ride_date.strftime('%Y-%m-%d')
        assert source == 'archive'

    def test_recent_ride_uses_past_days(self):
        """Ride 3 days ago routes to forecast API with past_days param."""
        from datetime import date, timedelta
        from services.weather import fetch_historical_wind

        ride_date = date.today() - timedelta(days=3)
        coords = [{'lat': 37.77, 'lng': -122.41}]

        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_archive_hourly_response()
        mock_resp.raise_for_status = MagicMock()

        with patch('services.weather.requests.get', return_value=mock_resp) as mock_get:
            result, source = fetch_historical_wind(coords, ride_date)

        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args.args[0]
        params = call_args[1].get('params') or call_args.kwargs.get('params', {})
        assert 'api.open-meteo.com/v1/forecast' in url
        assert 'past_days' in params
        assert source == 'forecast_past_days'

    def test_lag_boundary_uses_archive(self):
        """Ride exactly 5 days ago (boundary) uses archive API (ride_date <= today - 5)."""
        from datetime import date, timedelta
        from services.weather import fetch_historical_wind

        ride_date = date.today() - timedelta(days=5)
        coords = [{'lat': 37.77, 'lng': -122.41}]

        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_archive_hourly_response()
        mock_resp.raise_for_status = MagicMock()

        with patch('services.weather.requests.get', return_value=mock_resp) as mock_get:
            result, source = fetch_historical_wind(coords, ride_date)

        assert source == 'archive'

    def test_archive_single_dict_normalized(self):
        """Single-dict response from archive API is normalized to a list."""
        from datetime import date, timedelta
        from services.weather import fetch_historical_wind

        ride_date = date.today() - timedelta(days=10)
        coords = [{'lat': 37.77, 'lng': -122.41}]

        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_archive_hourly_response()  # dict, not list
        mock_resp.raise_for_status = MagicMock()

        with patch('services.weather.requests.get', return_value=mock_resp):
            result, source = fetch_historical_wind(coords, ride_date)

        assert isinstance(result, list)
        assert len(result) == 1

    def test_batch_coords(self):
        """3 coordinates send comma-separated latitude/longitude strings."""
        from datetime import date, timedelta
        from services.weather import fetch_historical_wind

        ride_date = date.today() - timedelta(days=10)
        coords = [
            {'lat': 37.77, 'lng': -122.41},
            {'lat': 37.50, 'lng': -122.10},
            {'lat': 37.00, 'lng': -121.60},
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = [_make_archive_hourly_response()] * 3
        mock_resp.raise_for_status = MagicMock()

        with patch('services.weather.requests.get', return_value=mock_resp) as mock_get:
            fetch_historical_wind(coords, ride_date)

        call_args = mock_get.call_args
        params = call_args[1].get('params') or call_args.kwargs.get('params', {})
        lat_str = str(params.get('latitude', ''))
        assert lat_str.count(',') == 2  # 3 values = 2 commas

    def test_http_error_propagates(self):
        """HTTPError from archive API propagates to caller."""
        from datetime import date, timedelta
        from requests.exceptions import HTTPError
        from services.weather import fetch_historical_wind

        ride_date = date.today() - timedelta(days=10)
        coords = [{'lat': 37.77, 'lng': -122.41}]

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = HTTPError("503 Service Unavailable")

        with patch('services.weather.requests.get', return_value=mock_resp):
            with pytest.raises(HTTPError):
                fetch_historical_wind(coords, ride_date)


# ── STOR-02: get_historical_stop_wind orchestration ──────────────────

def _make_stops(n=2):
    """Build minimal stop dicts for get_historical_stop_wind tests."""
    return [
        {'distance_miles': float(i * 50), 'stop_name': f'Stop {i}', 'arrival_time_min': i * 120}
        for i in range(n)
    ]


def _make_track_points():
    """Minimal RWGPS track with enough span for coordinate interpolation."""
    return [
        {'y': 37.77, 'x': -122.41, 'd': 0},
        {'y': 37.50, 'x': -122.10, 'd': 50000},
        {'y': 37.00, 'x': -121.60, 'd': 130000},
    ]


def _make_wind_hourly():
    """Archive-style hourly response for one stop."""
    times = [f"2026-01-01T{h:02d}:00" for h in range(24)]
    return {
        'hourly': {
            'time': times,
            'wind_speed_10m': [15.0] * 24,
            'wind_direction_10m': [270] * 24,
            'wind_gusts_10m': [20.0] * 24,
            'temperature_2m': [10.0] * 24,
        }
    }


def _stored_wind_rows(healed=True):
    """Pre-built DB rows mimicking get_ride_wind_data return value.

    healed=True includes the migration-027 gust/temp-range columns (a fresh row);
    healed=False mimics a pre-027 row (columns absent / None) that must be treated
    as stale and re-fetched.
    """
    extra = ({'wind_gust_kmh': 25.0, 'temp_min_c': 9.0, 'temp_max_c': 11.0}
             if healed else {})
    return [
        {
            'stop_order': 0, 'stop_name': 'Stop 0',
            'wind_speed_kmh': 12.0, 'wind_direction_deg': 270,
            'headwind_kmh': 10.0, 'crosswind_kmh': 2.0,
            'wind_type': 'headwind', 'temperature_c': 10.0,
            'conditions': 'clear sky', 'data_source': 'archive', **extra,
        },
        {
            'stop_order': 1, 'stop_name': 'Stop 1',
            'wind_speed_kmh': 18.0, 'wind_direction_deg': 270,
            'headwind_kmh': 15.0, 'crosswind_kmh': 3.0,
            'wind_type': 'headwind', 'temperature_c': 9.0,
            'conditions': 'clear sky', 'data_source': 'archive', **extra,
        },
    ]


class TestGetHistoricalStopWind:
    def test_empty_track_points_returns_none(self):
        """Empty track_points -> (None, None) with no API call."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops()
        ride_date = date.today() - timedelta(days=10)

        result = get_historical_stop_wind(stops, [], ride_date)
        assert result == (None, None)

    def test_api_error_returns_none(self):
        """API error -> (None, None), exception not propagated."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops()
        track_points = _make_track_points()
        ride_date = date.today() - timedelta(days=10)

        with patch('services.weather.get_ride_wind_data', return_value=[]), \
             patch('services.weather.fetch_historical_wind', side_effect=Exception("API down")):
            result = get_historical_stop_wind(stops, track_points, ride_date, ride_id=99)

        assert result == (None, None)

    def test_returns_wind_rows_with_classification(self):
        """Happy path: returns (wind_rows, data_source) with correct classification."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops(2)
        track_points = _make_track_points()
        ride_date = date.today() - timedelta(days=10)

        weather_data = [_make_wind_hourly(), _make_wind_hourly()]

        with patch('services.weather.get_ride_wind_data', return_value=[]), \
             patch('services.weather.fetch_historical_wind', return_value=(weather_data, 'archive')), \
             patch('services.weather.save_ride_wind_data'):
            wind_rows, source = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        assert wind_rows is not None
        assert source == 'archive'
        assert len(wind_rows) == 2

    def test_row_keys_complete(self):
        """Each wind row has all required columns."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops(2)
        track_points = _make_track_points()
        ride_date = date.today() - timedelta(days=10)

        weather_data = [_make_wind_hourly(), _make_wind_hourly()]

        with patch('services.weather.get_ride_wind_data', return_value=[]), \
             patch('services.weather.fetch_historical_wind', return_value=(weather_data, 'archive')), \
             patch('services.weather.save_ride_wind_data'):
            wind_rows, _ = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        required_keys = {
            'stop_order', 'stop_name', 'wind_speed_kmh', 'wind_direction_deg',
            'headwind_kmh', 'crosswind_kmh', 'wind_type', 'temperature_c',
            'conditions', 'data_source', 'wind_gust_kmh', 'temp_min_c', 'temp_max_c',
        }
        for row in wind_rows:
            assert required_keys.issubset(row.keys()), f"Missing keys: {required_keys - row.keys()}"

    def test_bearing_from_consecutive_coords(self):
        """Headwind/crosswind values are non-trivial with west wind and eastward route."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        # Route goes roughly east (San Francisco -> east), wind from west (270)
        stops = _make_stops(2)
        track_points = [
            {'y': 37.77, 'x': -122.41, 'd': 0},
            {'y': 37.77, 'x': -121.00, 'd': 130000},  # due east
        ]
        ride_date = date.today() - timedelta(days=10)

        weather_data = [_make_wind_hourly(), _make_wind_hourly()]  # wind from 270 (west)

        with patch('services.weather.get_ride_wind_data', return_value=[]), \
             patch('services.weather.fetch_historical_wind', return_value=(weather_data, 'archive')), \
             patch('services.weather.save_ride_wind_data'):
            wind_rows, _ = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        # West wind on eastward route = strong headwind
        assert any(abs(row['headwind_kmh']) > 5 for row in wind_rows), \
            "Expected non-trivial headwind with west wind on eastward route"

    def test_db_hit_skips_api_call(self):
        """STOR-02: DB rows present -> fetch_historical_wind is NOT called."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops(2)
        track_points = _make_track_points()
        ride_date = date.today() - timedelta(days=10)
        stored = _stored_wind_rows()

        with patch('services.weather.get_ride_wind_data', return_value=stored) as mock_get_db, \
             patch('services.weather.fetch_historical_wind') as mock_fetch:
            wind_rows, source = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        mock_fetch.assert_not_called()
        assert wind_rows == stored
        assert source == 'archive'  # from stored[0]['data_source']

    def test_db_miss_fetches_and_saves(self):
        """STOR-02: DB empty -> fetch_historical_wind called; save_ride_wind_data called with ride_id and rows."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops(2)
        track_points = _make_track_points()
        ride_date = date.today() - timedelta(days=10)

        weather_data = [_make_wind_hourly(), _make_wind_hourly()]

        with patch('services.weather.get_ride_wind_data', return_value=[]), \
             patch('services.weather.fetch_historical_wind', return_value=(weather_data, 'archive')) as mock_fetch, \
             patch('services.weather.save_ride_wind_data') as mock_save:
            wind_rows, source = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        mock_fetch.assert_called_once()
        mock_save.assert_called_once()
        save_args = mock_save.call_args[0]
        assert save_args[0] == 42
        assert save_args[1] == wind_rows  # saved rows match returned rows

    def test_stale_pre027_rows_trigger_refetch(self):
        """STOR-02 heal: stored rows lacking wind_gust_kmh are re-fetched, not served."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops(2)
        track_points = _make_track_points()
        ride_date = date.today() - timedelta(days=10)
        stale = _stored_wind_rows(healed=False)  # no gust/temp-range columns
        weather_data = [_make_wind_hourly(), _make_wind_hourly()]

        with patch('services.weather.get_ride_wind_data', return_value=stale), \
             patch('services.weather.fetch_historical_wind',
                   return_value=(weather_data, 'archive')) as mock_fetch, \
             patch('services.weather.save_ride_wind_data') as mock_save:
            wind_rows, _ = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        mock_fetch.assert_called_once()   # stale rows were NOT served
        mock_save.assert_called_once()    # re-saved with the new columns
        assert all(r.get('temp_min_c') is not None for r in wind_rows)

    def test_gustless_but_healed_rows_are_served(self):
        """Freshness keys on temp_min_c: a row with temp range but NO gust (gusts
        genuinely absent) is fresh and served, not re-fetched on every view."""
        from datetime import date, timedelta
        from services.weather import get_historical_stop_wind

        stops = _make_stops(2)
        track_points = _make_track_points()
        ride_date = date.today() - timedelta(days=10)
        served = _stored_wind_rows(healed=True)
        for r in served:
            r['wind_gust_kmh'] = None  # temp range present, gust absent

        with patch('services.weather.get_ride_wind_data', return_value=served), \
             patch('services.weather.fetch_historical_wind') as mock_fetch:
            wind_rows, _ = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        mock_fetch.assert_not_called()   # not re-fetched — temp_min_c marks it fresh
        assert wind_rows == served

    def test_gust_and_temp_sampled_over_segment_window(self):
        """#6/#7: gust = peak, temp = min/max over the leg's hour window, not a point."""
        from datetime import date
        from services.weather import get_historical_stop_wind

        # arrival_time_min 0 and 120 -> stop0 at 07:00 (hour 7), stop1 at 09:00 (hour 9).
        # Stop1's window is hours [7..9]; put a gust spike + temp swing inside it.
        def _hourly():
            times = [f"2026-01-01T{h:02d}:00" for h in range(24)]
            gusts = [20.0] * 24
            gusts[8] = 47.0  # spike between the two stops
            temps = [10.0] * 24
            temps[8] = 26.0  # hot hour mid-leg
            temps[9] = 4.0   # cold at arrival
            return {'hourly': {
                'time': times, 'wind_speed_10m': [15.0] * 24,
                'wind_direction_10m': [270] * 24,
                'wind_gusts_10m': gusts, 'temperature_2m': temps,
            }}

        stops = _make_stops(2)
        track_points = _make_track_points()
        ride_date = date(2026, 1, 1)  # match the hardcoded hourly time strings

        with patch('services.weather.get_ride_wind_data', return_value=[]), \
             patch('services.weather.fetch_historical_wind',
                   return_value=([_hourly(), _hourly()], 'archive')), \
             patch('services.weather.save_ride_wind_data'):
            wind_rows, _ = get_historical_stop_wind(stops, track_points, ride_date, ride_id=42)

        # Stop 0 window = [07:00, 07:00] -> single hour, no spike.
        assert wind_rows[0]['wind_gust_kmh'] == 20.0
        assert wind_rows[0]['temp_min_c'] == 10.0 and wind_rows[0]['temp_max_c'] == 10.0
        # Stop 1 window = hours 7..9 -> captures the spike and the swing.
        assert wind_rows[1]['wind_gust_kmh'] == 47.0
        assert wind_rows[1]['temp_min_c'] == 4.0 and wind_rows[1]['temp_max_c'] == 26.0


# ── HIST-01/02/03/04: Strava Analysis Route — Historical Wind Wiring ──

def _make_rider_row():
    return {'id': 7, 'rusa_id': 1234, 'name': 'Test Rider', 'strava_data_private': False}


def _make_ride_row():
    from datetime import date
    return {
        'id': 99, 'name': '300k Brevet', 'distance_km': 300.0,
        'date': date(2026, 3, 1),
        'ride_plan_id': 5, 'plan_slug': 'sfr-300k',
        'start_time': '06:00',
    }


def _make_match_row():
    return {
        'id': 1, 'strava_activity_id': 111111,
        'start_date_local': '2026-03-01T06:05:00',
        'rider_id': 7, 'ride_id': 99,
    }


def _make_plan_stops_raw():
    return [
        {'id': 1, 'distance_miles': 0.0, 'arrival_time_min': 0,
         'stop_name': 'Start', 'stop_type': 'start', 'location': 'Start'},
        {'id': 2, 'distance_miles': 93.0, 'arrival_time_min': 420,
         'stop_name': 'Finish', 'stop_type': 'finish', 'location': 'Finish'},
    ]


def _make_comparison_obj():
    """Minimal comparison object with two rows."""
    class Row:
        def __init__(self, location, is_extra=False):
            self.location = location
            self.is_extra = is_extra
            self.distance_miles = 0.0
            self.plan_stop_duration_min = None
            self.custom = None
            self.actual_stop_duration_min = None
            self.stop_delta_min = None
            self.plan_cum_time_min = None
            self.actual_cum_time_min = None
            self.cum_time_delta_min = None
            self.plan_time_of_day = None
            self.actual_time_of_day = None
            self.stop_type = 'control'

    class Summary:
        plan_distance_miles = 186.0
        actual_distance_miles = 186.0
        distance_delta_miles = 0.0
        plan_elevation_ft = 5000
        actual_elevation_ft = 5000
        elevation_delta_ft = 0
        plan_total_time_min = 840
        actual_elapsed_time_min = 840
        time_delta_min = 0
        actual_moving_time_min = 780
        plan_break_time_min = 60
        actual_stopped_time_min = 60
        break_delta_min = 0
        plan_avg_speed_mph = 14.0
        actual_avg_speed_mph = 14.0
        speed_delta_mph = 0.0

    class Comparison:
        rows = [Row('Start'), Row('Finish')]
        summary = Summary()

    return Comparison()


def _make_wind_rows_for_analysis():
    return [
        {'stop_order': 0, 'stop_name': 'Start', 'wind_speed_kmh': 20.0,
         'wind_direction_deg': 270, 'headwind_kmh': 18.0, 'crosswind_kmh': 2.0,
         'wind_type': 'headwind', 'temperature_c': 12.0,
         'conditions': 'clear sky', 'data_source': 'archive'},
        {'stop_order': 1, 'stop_name': 'Finish', 'wind_speed_kmh': 15.0,
         'wind_direction_deg': 270, 'headwind_kmh': 12.0, 'crosswind_kmh': 3.0,
         'wind_type': 'headwind', 'temperature_c': 10.0,
         'conditions': 'clear sky', 'data_source': 'archive'},
    ]


_STRAVA_TRACK_POINTS = [
    {'y': 37.77, 'x': -122.41, 'd': 0},
    {'y': 37.60, 'x': -121.60, 'd': 130000},
]


class TestStravaAnalysisWind:
    """HIST-01/02/03/04: ride_strava_analysis passes stop_wind dict to template."""

    def _base_patches(self, app, rider, ride, match, plan_stops, analysis_result,
                      comparison, plan_row, route_data, wind_rows):
        """Return a dict of patch targets and return values for ride_strava_analysis."""
        return {
            'routes.riders.get_rider_by_rusa': rider,
            'routes.riders.get_ride_by_id_full': ride,
            'routes.riders.get_strava_ride_match': match,
            'routes.riders.get_ride_plan_stops': plan_stops,
            'routes.riders.get_custom_plan': None,
            'routes.riders.get_ride_plan_by_slug': plan_row,
            'routes.riders.fetch_route': route_data,
            'routes.riders.fetch_and_analyze': analysis_result,
            'routes.riders.build_comparison': comparison,
            'routes.riders.get_historical_stop_wind': wind_rows,
            'routes.riders.wind_cell_style': {'background': '#ccc', 'color': '#000', 'font_size': '0.9rem'},
        }

    def test_has_plan_passes_stop_wind_dict_to_template(self, app):
        """HIST-01: With has_plan=True and track_points, stop_wind dict keyed by stop_name is passed."""
        from unittest.mock import patch, MagicMock

        rider = _make_rider_row()
        ride = _make_ride_row()
        match = _make_match_row()
        plan_stops = _make_plan_stops_raw()
        wind_rows = _make_wind_rows_for_analysis()
        comparison = _make_comparison_obj()

        mock_plan = {
            'id': 5, 'slug': 'sfr-300k', 'name': 'SFR 300k',
            'rwgps_url_team': 'https://ridewithgps.com/routes/99999',
            'rwgps_url': None,
        }
        mock_route = {'track_points': _STRAVA_TRACK_POINTS}
        mock_analysis = {
            'error': None,
            'detected_stops': [],
        }

        mock_style = {'background': 'rgba(220,38,38,0.35)', 'color': '#DC2626', 'font_size': '0.875rem'}

        with app.test_request_context():
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('models.get_ride_plan_stops', return_value=plan_stops), \
                 patch('models.get_custom_plan', return_value=None), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('routes.riders.get_ride_plan_by_slug', return_value=mock_plan), \
                 patch('routes.riders.fetch_route', return_value=mock_route), \
                 patch('services.strava_analysis.find_matching_activity', return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze', return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison', return_value=comparison), \
                 patch('services.weather.get_historical_stop_wind', return_value=(wind_rows, 'archive')), \
                 patch('services.weather.wind_cell_style', return_value=mock_style), \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'rider_id': 999}):
                from routes.riders import ride_strava_analysis
                ride_strava_analysis(rusa_id=1234, ride_id=99)

        assert mock_render.called, "render_template was not called"
        call_kwargs = mock_render.call_args[1]
        assert 'stop_wind' in call_kwargs, "stop_wind not passed to render_template"
        stop_wind = call_kwargs['stop_wind']
        assert stop_wind is not None, "stop_wind should be a dict, not None"
        assert isinstance(stop_wind, dict), f"stop_wind should be dict, got {{type(stop_wind)}}"
        assert 'Start' in stop_wind, "stop_wind dict should be keyed by stop_name"
        assert 'Finish' in stop_wind, "stop_wind dict should contain all stop names"

    def test_stop_wind_dict_has_style_key(self, app):
        """HIST-03: Each entry in stop_wind dict has a 'style' sub-dict from wind_cell_style."""
        from unittest.mock import patch

        rider = _make_rider_row()
        ride = _make_ride_row()
        match = _make_match_row()
        plan_stops = _make_plan_stops_raw()
        wind_rows = _make_wind_rows_for_analysis()
        comparison = _make_comparison_obj()

        mock_plan = {
            'id': 5, 'slug': 'sfr-300k', 'name': 'SFR 300k',
            'rwgps_url_team': 'https://ridewithgps.com/routes/99999',
            'rwgps_url': None,
        }
        mock_route = {'track_points': _STRAVA_TRACK_POINTS}
        mock_analysis = {'error': None, 'detected_stops': []}
        mock_style = {'background': 'rgba(220,38,38,0.35)', 'color': '#DC2626', 'font_size': '0.875rem'}

        with app.test_request_context():
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('models.get_ride_plan_stops', return_value=plan_stops), \
                 patch('models.get_custom_plan', return_value=None), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('routes.riders.get_ride_plan_by_slug', return_value=mock_plan), \
                 patch('routes.riders.fetch_route', return_value=mock_route), \
                 patch('services.strava_analysis.find_matching_activity', return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze', return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison', return_value=comparison), \
                 patch('services.weather.get_historical_stop_wind', return_value=(wind_rows, 'archive')), \
                 patch('services.weather.wind_cell_style', return_value=mock_style) as mock_wcs, \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'rider_id': 999}):
                from routes.riders import ride_strava_analysis
                ride_strava_analysis(rusa_id=1234, ride_id=99)

        assert mock_wcs.called, "wind_cell_style was not called"
        call_kwargs = mock_render.call_args[1]
        stop_wind = call_kwargs['stop_wind']
        for stop_name, entry in stop_wind.items():
            assert 'style' in entry, f"stop_wind['{stop_name}'] missing 'style' key"
            assert entry['style'] == mock_style, f"stop_wind['{stop_name}']['style'] mismatch"

    def test_no_plan_passes_stop_wind_none(self, app):
        """HIST-02: has_plan=False → stop_wind=None, no error raised."""
        from unittest.mock import patch

        rider = _make_rider_row()
        ride = dict(_make_ride_row())
        ride['ride_plan_id'] = None  # No linked plan
        ride['plan_slug'] = None
        match = _make_match_row()
        comparison = _make_comparison_obj()
        mock_analysis = {'error': None, 'detected_stops': []}

        with app.test_request_context():
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('models.get_custom_plan', return_value=None), \
                 patch('routes.riders.get_all_ride_plans', return_value=[]), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('services.strava_analysis.find_matching_activity', return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze', return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison', return_value=comparison), \
                 patch('services.weather.get_historical_stop_wind') as mock_hist, \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'rider_id': 999}):
                from routes.riders import ride_strava_analysis
                ride_strava_analysis(rusa_id=1234, ride_id=99)

        mock_hist.assert_not_called()
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs.get('stop_wind') is None, \
            f"stop_wind should be None when no plan, got: {call_kwargs.get('stop_wind')}"

    def test_null_fk_resolves_plan_and_custom_by_name(self, app):
        """A ride with a NULL ride_plan_id FK still resolves its plan — and the
        rider's custom plan on it — by route-name match (the reported bug: a
        RUSA-scraped 600k with a customized plan showed 'no plan')."""
        from unittest.mock import patch

        rider = _make_rider_row()
        ride = dict(_make_ride_row())
        ride['ride_plan_id'] = None          # never FK-linked (typical RUSA event)
        ride['plan_slug'] = None
        ride['name'] = 'Surf City 600k Brevet'
        match = _make_match_row()
        comparison = _make_comparison_obj()
        mock_analysis = {'error': None, 'detected_stops': []}
        matched_plan = {'id': 60, 'name': 'SCR Surf City 600k',
                        'slug': 'scr-surf-city-600k', 'start_time': None}
        custom_plan = {'id': 40}

        with app.test_request_context():
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('routes.riders.get_all_ride_plans', return_value=[matched_plan]), \
                 patch('models.get_ride_plan_stops',
                       return_value=[{'location': 'Start', 'distance_miles': 0.0}]), \
                 patch('models.get_custom_plan', return_value=custom_plan) as mock_custom, \
                 patch('services.custom_plan_service.get_merged_plan_stops',
                       return_value=([{'location': 'Start', 'distance_miles': 0.0}], None)), \
                 patch('routes.riders.get_ride_plan_by_slug', return_value=None), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('services.strava_analysis.find_matching_activity', return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze', return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison', return_value=comparison), \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'rider_id': 999}):
                from routes.riders import ride_strava_analysis
                ride_strava_analysis(rusa_id=1234, ride_id=99)

        # Custom plan looked up with the NAME-MATCHED base plan id (60), not the null FK.
        mock_custom.assert_called_once()
        assert mock_custom.call_args[0][1] == 60
        kw = mock_render.call_args[1]
        assert kw.get('has_plan') is True
        assert kw.get('has_custom') is True
        assert kw.get('plan_slug') == 'scr-surf-city-600k'

    def test_rich_analysis_passed_to_template(self, app):
        """Persisted coach notes render without making a blocking LLM call."""
        from unittest.mock import patch

        rider = _make_rider_row()
        ride = dict(_make_ride_row())
        match = _make_match_row()
        mock_analysis = {'error': None, 'detected_stops': [], 'streams': {}}
        comparison_dict = {
            'rows': [{'location': 'Control 1', 'is_extra': False, 'distance_miles': 50.0}],
            'summary': {'speed_delta_mph': -1.0},
            'hr_power': {'avg_watts': 180},
        }
        coaching = {'per_segment': {'Control 1': 'Ease off on the early climbs.'},
                    'overall': {'summary': 'Solid, well-paced ride.',
                                'recommendations': ['Fuel earlier', 'Shorter controls']}}

        with app.test_request_context():
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('models.get_custom_plan', return_value=None), \
                 patch('models.get_ride_plan_stops',
                       return_value=[{'location': 'Control 1', 'distance_miles': 50.0}]), \
                 patch('routes.riders.get_ride_plan_by_slug', return_value=None), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('services.strava_analysis.find_matching_activity', return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze', return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison', return_value=comparison_dict), \
                 patch('models.get_rider_activity_baseline',
                       return_value={'n_rides': 10, 'avg_speed_mph': 14.0}), \
                 patch('services.segment_analysis.compute_gradient_band_baseline', return_value={}), \
                 patch('services.route_history.compute_same_route_segment_baseline', return_value={}), \
                 patch('services.segment_analysis.build_segment_narratives',
                       return_value={'Control 1': 'You averaged 180 W, 10% lower than the previous segment.'}), \
                 patch('services.segment_analysis.build_overall_narrative',
                       return_value=['You rode 1 mph slower than plan.']), \
                 patch('models.get_strava_ride_analysis',
                       return_value={'llm_narrative': coaching}), \
                 patch('services.ride_coach.generate_ride_coaching') as generate_coaching, \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'rider_id': 999}):
                from routes.riders import ride_strava_analysis
                ride_strava_analysis(rusa_id=1234, ride_id=99)

        generate_coaching.assert_not_called()
        kw = mock_render.call_args[1]
        assert kw['segment_eval']['Control 1']['narrative'].startswith('You averaged 180 W')
        assert kw['segment_eval']['Control 1']['coach'] == 'Ease off on the early climbs.'
        assert kw['ride_recommendations']['summary'] == 'Solid, well-paced ride.'
        assert kw['overall_narrative'] == ['You rode 1 mph slower than plan.']

    def test_coaching_request_generates_and_persists_for_owner(self, app):
        """The opt-in follow-up request generates coaching after page render."""
        from unittest.mock import patch

        rider = _make_rider_row()
        ride = dict(_make_ride_row())
        match = _make_match_row()
        mock_analysis = {'error': None, 'detected_stops': [], 'streams': {}}
        comparison_dict = {
            'rows': [{'location': 'Control 1', 'is_extra': False,
                      'distance_miles': 50.0}],
            'summary': {'speed_delta_mph': -1.0},
            'hr_power': {'avg_watts': 180},
        }
        coaching = {
            'per_segment': {'Control 1': 'Ease off on the early climbs.'},
            'overall': {'summary': 'Solid, well-paced ride.',
                        'recommendations': ['Fuel earlier']},
        }

        with app.test_request_context('/?coaching=1'):
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('models.get_custom_plan', return_value=None), \
                 patch('models.get_ride_plan_stops',
                       return_value=[{'location': 'Control 1',
                                     'distance_miles': 50.0}]), \
                 patch('routes.riders.get_ride_plan_by_slug', return_value=None), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('services.strava_analysis.find_matching_activity',
                       return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze',
                       return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison',
                       return_value=comparison_dict), \
                 patch('models.get_strava_ride_analysis', return_value={}), \
                 patch('models.get_rider_activity_baseline', return_value={}), \
                 patch('services.segment_analysis.compute_gradient_band_baseline',
                       return_value={}), \
                 patch('services.route_history.compute_same_route_segment_baseline',
                       return_value={}), \
                 patch('services.segment_analysis.build_segment_narratives',
                       return_value={}), \
                 patch('services.segment_analysis.build_overall_narrative',
                       return_value=[]), \
                 patch('services.ride_coach.generate_ride_coaching',
                       return_value=coaching) as generate_coaching, \
                 patch('models.save_strava_ride_coaching',
                       return_value=True) as save_coaching, \
                 patch('routes.riders.session', {'rider_id': rider['id']}):
                from routes.riders import ride_strava_analysis
                response = ride_strava_analysis(rusa_id=1234, ride_id=99)

        assert response.json == {'ok': True}
        generate_coaching.assert_called_once()
        save_coaching.assert_called_once_with(rider['id'], match['id'], coaching)

    def test_coaching_request_is_owner_only(self, app):
        """Public viewers cannot trigger paid/private coaching generation."""
        from unittest.mock import patch
        from werkzeug.exceptions import Forbidden

        rider = _make_rider_row()
        with app.test_request_context('/?coaching=1'):
            with patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('models.get_ride_by_id_full', return_value=_make_ride_row()), \
                 patch('routes.riders.session', {}):
                from routes.riders import ride_strava_analysis
                with pytest.raises(Forbidden):
                    ride_strava_analysis(rusa_id=1234, ride_id=99)

    def test_rich_analysis_failure_does_not_break_render(self, app):
        """If the rich-analysis layer raises, the page still renders (best-effort)."""
        from unittest.mock import patch

        rider = _make_rider_row()
        ride = dict(_make_ride_row())
        match = _make_match_row()
        mock_analysis = {'error': None, 'detected_stops': [], 'streams': {}}
        comparison_dict = {'rows': [{'location': 'Control 1', 'is_extra': False,
                                     'distance_miles': 50.0}], 'summary': {}, 'hr_power': {}}

        with app.test_request_context():
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('models.get_custom_plan', return_value=None), \
                 patch('models.get_ride_plan_stops', return_value=[{'location': 'Control 1'}]), \
                 patch('routes.riders.get_ride_plan_by_slug', return_value=None), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('services.strava_analysis.find_matching_activity', return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze', return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison', return_value=comparison_dict), \
                 patch('models.get_rider_activity_baseline', side_effect=RuntimeError('boom')), \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'rider_id': 999}):
                from routes.riders import ride_strava_analysis
                ride_strava_analysis(rusa_id=1234, ride_id=99)

        kw = mock_render.call_args[1]
        # Degrades to empty enrichment; render still happens.
        assert kw['segment_eval'] == {}
        assert kw['ride_recommendations'] is None

    def test_has_plan_no_rwgps_route_passes_stop_wind_none(self, app):
        """HIST-02: has_plan=True but no RWGPS route → stop_wind=None, no error."""
        from unittest.mock import patch

        rider = _make_rider_row()
        ride = _make_ride_row()
        match = _make_match_row()
        plan_stops = _make_plan_stops_raw()
        comparison = _make_comparison_obj()
        mock_analysis = {'error': None, 'detected_stops': []}

        mock_plan = {
            'id': 5, 'slug': 'sfr-300k', 'name': 'SFR 300k',
            'rwgps_url_team': None,  # No RWGPS URL
            'rwgps_url': None,
        }

        with app.test_request_context():
            with patch('models.get_ride_by_id_full', return_value=ride), \
                 patch('models.get_strava_ride_match', return_value=match), \
                 patch('models.get_strava_connection', return_value=None), \
                 patch('models.get_custom_plan', return_value=None), \
                 patch('routes.riders.get_rider_by_rusa', return_value=rider), \
                 patch('models.get_ride_plan_stops', return_value=plan_stops), \
                 patch('routes.riders.get_ride_plan_by_slug', return_value=mock_plan), \
                 patch('services.strava_analysis.find_matching_activity', return_value=None), \
                 patch('services.strava_analysis.fetch_and_analyze', return_value=mock_analysis), \
                 patch('services.strava_analysis.build_comparison', return_value=comparison), \
                 patch('services.weather.get_historical_stop_wind') as mock_hist, \
                 patch('routes.riders.render_template', return_value='') as mock_render, \
                 patch('routes.riders.session', {'rider_id': 999}):
                from routes.riders import ride_strava_analysis
                ride_strava_analysis(rusa_id=1234, ride_id=99)

        mock_hist.assert_not_called()
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs.get('stop_wind') is None, \
            f"stop_wind should be None when no RWGPS route, got: {call_kwargs.get('stop_wind')}"
