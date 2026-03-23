"""Tests for get_rider_participation model function — plan_slug column."""
from unittest.mock import patch, MagicMock
import pytest


def _make_mock_row(**kwargs):
    """Create a dict-like row for mocking _execute().fetchall() results."""
    defaults = {
        'status': 'FINISHED',
        'finish_time': None,
        'ride_id': 1,
        'ride_name': 'Test Ride',
        'date': '2025-06-01',
        'distance_km': 200,
        'elevation_ft': 5000,
        'ft_per_mile': 50,
        'rwgps_url': None,
        'ride_plan_id': None,
        'club_code': 'ACP',
        'plan_slug': None,
    }
    return {**defaults, **kwargs}


class TestRiderParticipationPlanSlug:
    """Verify get_rider_participation returns plan_slug via LEFT JOIN ride_plan."""

    def test_result_rows_have_plan_slug_key(self):
        """get_rider_participation rows must include a 'plan_slug' key."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [_make_mock_row(plan_slug=None)]

        with patch('models._execute', return_value=mock_cur):
            from models import get_rider_participation
            rows = get_rider_participation(1, 10)

        assert len(rows) == 1
        assert 'plan_slug' in rows[0]

    def test_ride_with_linked_plan_returns_slug(self):
        """get_rider_participation returns the plan slug for rides with a linked ride_plan."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [_make_mock_row(ride_plan_id=5, plan_slug='paris-brest-paris-2025')]

        with patch('models._execute', return_value=mock_cur):
            from models import get_rider_participation
            rows = get_rider_participation(1, 10)

        assert rows[0]['plan_slug'] == 'paris-brest-paris-2025'

    def test_ride_without_linked_plan_returns_none_slug(self):
        """get_rider_participation returns plan_slug=None for rides without a linked ride_plan."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [_make_mock_row(ride_plan_id=None, plan_slug=None)]

        with patch('models._execute', return_value=mock_cur):
            from models import get_rider_participation
            rows = get_rider_participation(1, 10)

        assert rows[0]['plan_slug'] is None

    def test_sql_contains_plan_slug_column(self):
        """get_rider_participation SQL must SELECT rp.slug as plan_slug."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []

        with patch('models._execute', return_value=mock_cur) as mock_execute:
            from models import get_rider_participation
            get_rider_participation(1, 10)

        sql_called = mock_execute.call_args[0][0]
        assert 'rp.slug as plan_slug' in sql_called

    def test_sql_contains_left_join_ride_plan(self):
        """get_rider_participation SQL must LEFT JOIN ride_plan rp."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []

        with patch('models._execute', return_value=mock_cur) as mock_execute:
            from models import get_rider_participation
            get_rider_participation(1, 10)

        sql_called = mock_execute.call_args[0][0]
        assert 'LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id' in sql_called
