"""Tests for the Compare with Cohort feature.

Covers: _dynamic_display_range, build_cohort_stats (percentile, HR normalization,
display strings, insights), and the /ride/<id>/cohort route.
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_riders(overrides_per_rider=None):
    """Return a minimal list of 3 rider dicts matching get_ride_cohort_stats schema."""
    base = [
        {
            'rider_id': 1, 'first_name': 'Alice', 'last_name': 'Smith',
            'elapsed_time': 36000, 'moving_time': 34200, 'stopped_time': 1800,
            'average_speed': 5.5, 'average_heartrate': 148.0, 'max_heartrate': 172.0,
            'total_elevation_gain': 1200.0, 'suffer_score': 280.0,
            'average_cadence': 85.0,
            'average_watts': None, 'weighted_average_watts': None, 'device_watts': False,
        },
        {
            'rider_id': 2, 'first_name': 'Bob', 'last_name': 'Jones',
            'elapsed_time': 38000, 'moving_time': 35500, 'stopped_time': 2500,
            'average_speed': 5.2, 'average_heartrate': 155.0, 'max_heartrate': 179.0,
            'total_elevation_gain': 1250.0, 'suffer_score': 310.0,
            'average_cadence': 78.0,
            'average_watts': None, 'weighted_average_watts': None, 'device_watts': False,
        },
        {
            'rider_id': 3, 'first_name': 'Carol', 'last_name': 'Lee',
            'elapsed_time': 40000, 'moving_time': 37000, 'stopped_time': 3000,
            'average_speed': 5.0, 'average_heartrate': 162.0, 'max_heartrate': 185.0,
            'total_elevation_gain': 1300.0, 'suffer_score': 340.0,
            'average_cadence': 72.0,
            'average_watts': None, 'weighted_average_watts': None, 'device_watts': False,
        },
    ]
    if overrides_per_rider:
        for i, ov in enumerate(overrides_per_rider):
            base[i].update(ov)
    return base


# ── _dynamic_display_range ───────────────────────────────────────────────────

class TestDynamicDisplayRange:
    def test_padding_applied_to_both_sides(self):
        """Display range extends beyond data min/max on both sides."""
        from services.strava_analysis import _dynamic_display_range
        d_min, d_max = _dynamic_display_range('average_heartrate', 148.0, 162.0)
        assert d_min < 148.0
        assert d_max > 162.0

    def test_min_pad_kicks_in_for_tight_spread(self):
        """When data spread is very small, min-pad ensures a usable axis width."""
        from services.strava_analysis import _dynamic_display_range
        # spread = 1 bpm, 15% = 0.15 — well below the 4 bpm min-pad
        d_min, d_max = _dynamic_display_range('average_heartrate', 150.0, 151.0)
        assert (d_max - d_min) >= 8.0  # at least 2× min-pad of 4 bpm

    def test_floor_clamps_display_min(self):
        """Display min never goes below the metric-specific floor."""
        from services.strava_analysis import _dynamic_display_range
        # stopped_time floor is 0
        d_min, d_max = _dynamic_display_range('stopped_time', 0.0, 300.0)
        assert d_min == 0.0

    def test_identical_values_produce_valid_range(self):
        """All riders with the same value still produces a non-zero axis width."""
        from services.strava_analysis import _dynamic_display_range
        d_min, d_max = _dynamic_display_range('elapsed_time', 36000.0, 36000.0)
        assert d_max > d_min


# ── build_cohort_stats: core stats ───────────────────────────────────────────

class TestBuildCohortStats:
    def test_all_metrics_present(self):
        """Result contains entries for all 11 tracked metrics."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=1)
        expected = {
            'elapsed_time', 'moving_time', 'stopped_time', 'average_speed',
            'average_cadence',
            'average_heartrate', 'max_heartrate', 'total_elevation_gain',
            'suffer_score', 'average_watts', 'weighted_average_watts',
        }
        assert expected == set(stats.keys())

    def test_has_data_false_when_all_null(self):
        """Metric with all-None values returns has_data=False."""
        from services.strava_analysis import build_cohort_stats
        riders = _make_riders()  # average_watts is None for all
        stats = build_cohort_stats(riders, current_rider_id=1)
        assert stats['average_watts']['has_data'] is False

    def test_user_value_populated_for_logged_in_rider(self):
        """user_value matches the logged-in rider's data."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=1)
        assert stats['elapsed_time']['user_value'] == 36000.0

    def test_user_value_none_when_not_in_cohort(self):
        """user_value is None when current rider is absent from the cohort."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=99)
        assert stats['elapsed_time']['user_value'] is None
        assert stats['elapsed_time']['percentile'] is None
        assert stats['elapsed_time']['bar_position'] is None

    def test_percentile_fastest_elapsed_time(self):
        """Fastest of 3 riders beats the other 2, so percentile = round(2/3*100) = 67."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=1)  # rider 1 is fastest
        assert stats['elapsed_time']['percentile'] == 67

    def test_percentile_slowest_elapsed_time(self):
        """Slowest rider beats 0% of the cohort on elapsed_time."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=3)  # rider 3 is slowest
        assert stats['elapsed_time']['percentile'] == 0

    def test_percentile_none_for_reference_metric(self):
        """Reference-direction metrics (HR) have no percentile."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=1)
        assert stats['average_heartrate']['percentile'] is None

    def test_bar_position_identical_values(self):
        """All-identical values fall back to bar_position=50."""
        from services.strava_analysis import build_cohort_stats
        riders = _make_riders([
            {'stopped_time': 1800},
            {'stopped_time': 1800},
            {'stopped_time': 1800},
        ])
        stats = build_cohort_stats(riders, current_rider_id=1)
        assert stats['stopped_time']['bar_position'] == 50


# ── HR normalization ─────────────────────────────────────────────────────────

class TestHRNormalization:
    def test_avg_and_max_hr_share_display_range(self):
        """After normalization, avg_heartrate and max_heartrate share identical display bounds."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=1)
        avg = stats['average_heartrate']
        mx = stats['max_heartrate']
        assert avg['display_min'] == mx['display_min']
        assert avg['display_max'] == mx['display_max']

    def test_shared_range_spans_both_datasets(self):
        """Shared range is wide enough to contain both avg HR and max HR data."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=1)
        avg = stats['average_heartrate']
        mx = stats['max_heartrate']
        # avg HR data: 148–162, max HR data: 172–185 — shared range must cover both
        assert avg['display_min'] <= 148.0
        assert avg['display_max'] >= 185.0


# ── Insights ─────────────────────────────────────────────────────────────────

class TestCohortInsights:
    def test_stopped_time_insight_triggered(self):
        """Insight fires when user's stopped time is >15 min above median."""
        from services.strava_analysis import build_cohort_stats
        # Rider 3 has 3000s stopped; riders 1+2 have 1800+2500 → median 2500
        # diff = 500s, below threshold — push rider 3 well over
        riders = _make_riders([
            {'stopped_time': 600},
            {'stopped_time': 600},
            {'stopped_time': 2700},  # 2100s = 35 min above median of 600
        ])
        stats = build_cohort_stats(riders, current_rider_id=3)
        assert stats['stopped_time']['insight'] is not None
        assert 'minutes' in stats['stopped_time']['insight']

    def test_stopped_time_no_insight_within_threshold(self):
        """No insight when stopped time difference is <= 15 min."""
        from services.strava_analysis import build_cohort_stats
        riders = _make_riders([
            {'stopped_time': 1800},
            {'stopped_time': 1800},
            {'stopped_time': 2400},  # 600s = 10 min above median — under threshold
        ])
        stats = build_cohort_stats(riders, current_rider_id=3)
        assert stats['stopped_time']['insight'] is None

    def test_cadence_insight_fires_when_below_median(self):
        """Insight fires when user cadence is more than 5 rpm below the median."""
        from services.strava_analysis import build_cohort_stats
        riders = _make_riders([
            {'average_cadence': 95.0},
            {'average_cadence': 90.0},
            {'average_cadence': 70.0},  # rider 3: 22.5 rpm below median (91) — triggers insight
        ])
        stats = build_cohort_stats(riders, current_rider_id=3)
        assert stats['average_cadence']['insight'] is not None
        assert 'cadence' in stats['average_cadence']['insight'].lower()

    def test_cadence_insight_suppressed_when_near_median(self):
        """No insight when user cadence is within 5 rpm of the median."""
        from services.strava_analysis import build_cohort_stats
        riders = _make_riders([
            {'average_cadence': 88.0},
            {'average_cadence': 85.0},
            {'average_cadence': 83.0},  # rider 3: 2 rpm below median — under threshold
        ])
        stats = build_cohort_stats(riders, current_rider_id=3)
        assert stats['average_cadence']['insight'] is None

    def test_cadence_card_hidden_when_no_cadence_data(self):
        """Metric shows has_data=False when all riders have None cadence."""
        from services.strava_analysis import build_cohort_stats
        riders = _make_riders([
            {'average_cadence': None},
            {'average_cadence': None},
            {'average_cadence': None},
        ])
        stats = build_cohort_stats(riders, current_rider_id=1)
        assert stats['average_cadence']['has_data'] is False

    def test_no_insight_when_user_not_in_cohort(self):
        """Insights are all None when current_rider_id is absent."""
        from services.strava_analysis import build_cohort_stats
        stats = build_cohort_stats(_make_riders(), current_rider_id=99)
        assert stats['stopped_time']['insight'] is None
        assert stats['average_speed']['insight'] is None
        assert stats['average_cadence']['insight'] is None
        assert stats['average_heartrate']['insight'] is None


# ── Route ────────────────────────────────────────────────────────────────────

class TestCohortRoute:
    def _login(self, client):
        """Set the session keys required by user_login_required."""
        with client.session_transaction() as sess:
            sess['user_id'] = 'test@example.com'
            sess['rider_id'] = 1

    def test_404_for_unknown_ride(self, client):
        """Returns 404 when ride does not exist."""
        self._login(client)
        with patch('routes.riders.get_ride_by_id', return_value=None):
            resp = client.get('/ride/9999/cohort')
        assert resp.status_code == 404

    def test_login_required(self, client):
        """Unauthenticated request (no session) redirects to login."""
        resp = client.get('/ride/1/cohort')
        assert resp.status_code == 302

    def test_renders_with_insufficient_riders(self, client):
        """Page renders without error when only 1 rider has Strava data."""
        self._login(client)
        ride = {'id': 1, 'name': 'Test 200k', 'date': '2025-06-01', 'distance_km': 200}
        breakdown = {'total_finished': 3, 'strava_linked': 1, 'private': 0, 'compared': 1}
        with patch('routes.riders.get_ride_by_id', return_value=ride), \
             patch('routes.riders._auto_match_cohort_riders'), \
             patch('routes.riders.get_ride_cohort_stats', return_value=[_make_riders()[0]]), \
             patch('routes.riders.get_ride_cohort_breakdown', return_value=breakdown):
            resp = client.get('/ride/1/cohort')
        assert resp.status_code == 200
        assert b'Not Enough Riders' in resp.data

    def test_renders_full_comparison(self, client):
        """Page renders successfully with 2+ riders."""
        self._login(client)
        ride = {'id': 1, 'name': 'Test 200k', 'date': '2025-06-01', 'distance_km': 200}
        breakdown = {'total_finished': 3, 'strava_linked': 3, 'private': 0, 'compared': 3}
        with patch('routes.riders.get_ride_by_id', return_value=ride), \
             patch('routes.riders._auto_match_cohort_riders'), \
             patch('routes.riders.get_ride_cohort_stats', return_value=_make_riders()), \
             patch('routes.riders.get_ride_cohort_breakdown', return_value=breakdown):
            resp = client.get('/ride/1/cohort')
        assert resp.status_code == 200
        assert b'Cohort Analysis' in resp.data
