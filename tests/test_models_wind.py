"""Tests for ride_wind_data model functions — get_ride_wind_data and save_ride_wind_data."""
from unittest.mock import patch, MagicMock, call
import psycopg2.extras
import pytest


# ── Sample data ─────────────────────────────────────────────────────

SAMPLE_WIND_ROW = {
    'id': 1,
    'ride_id': 42,
    'stop_order': 0,
    'stop_name': 'Start',
    'wind_speed_kmh': 18.5,
    'wind_direction_deg': 270,
    'headwind_kmh': 12.3,
    'crosswind_kmh': 5.1,
    'wind_type': 'headwind',
    'temperature_c': 14.2,
    'conditions': 'Partly cloudy',
    'data_source': 'archive',
    'fetched_at': '2026-03-20T08:00:00',
}

SAMPLE_WIND_ROWS_MULTI = [
    {
        'ride_id': 42,
        'stop_order': 0,
        'stop_name': 'Start',
        'wind_speed_kmh': 18.5,
        'wind_direction_deg': 270,
        'headwind_kmh': 12.3,
        'crosswind_kmh': 5.1,
        'wind_type': 'headwind',
        'temperature_c': 14.2,
        'conditions': 'Partly cloudy',
        'data_source': 'archive',
    },
    {
        'ride_id': 42,
        'stop_order': 1,
        'stop_name': 'Control 1',
        'wind_speed_kmh': 20.0,
        'wind_direction_deg': 260,
        'headwind_kmh': 15.0,
        'crosswind_kmh': 4.0,
        'wind_type': 'headwind',
        'temperature_c': 13.0,
        'conditions': 'Clear',
        'data_source': 'archive',
    },
    {
        'ride_id': 42,
        'stop_order': 2,
        'stop_name': 'Finish',
        'wind_speed_kmh': 10.0,
        'wind_direction_deg': 90,
        'headwind_kmh': -8.0,
        'crosswind_kmh': 3.0,
        'wind_type': 'tailwind',
        'temperature_c': 16.0,
        'conditions': 'Sunny',
        'data_source': 'archive',
    },
]


def _make_mock_cursor(rows):
    """Create a mock cursor that returns the given rows from fetchall()."""
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = rows
    return mock_cur


def _make_mock_conn(rows=None):
    """Create a mock DB connection with a cursor returning given rows."""
    mock_conn = MagicMock()
    mock_cur = _make_mock_cursor(rows or [])
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


# ── TestGetRideWindData ──────────────────────────────────────────────

class TestGetRideWindData:

    def test_returns_empty_list_when_no_rows(self):
        """get_ride_wind_data returns [] when ride has no stored wind rows."""
        mock_cur = _make_mock_cursor([])
        with patch('models.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_get_db.return_value = mock_conn

            from models import get_ride_wind_data
            result = get_ride_wind_data(999)

        assert result == []

    def test_returns_rows_for_known_ride(self):
        """get_ride_wind_data returns stored rows for a ride_id."""
        expected = [SAMPLE_WIND_ROW]
        mock_cur = _make_mock_cursor(expected)
        with patch('models.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_get_db.return_value = mock_conn

            from models import get_ride_wind_data
            result = get_ride_wind_data(42)

        assert result == expected

    def test_returns_list_type(self):
        """get_ride_wind_data always returns a list, not a cursor."""
        mock_cur = _make_mock_cursor([SAMPLE_WIND_ROW])
        with patch('models.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_get_db.return_value = mock_conn

            from models import get_ride_wind_data
            result = get_ride_wind_data(42)

        assert isinstance(result, list)

    def test_queries_with_correct_ride_id(self):
        """get_ride_wind_data passes ride_id as parameter to SELECT."""
        mock_cur = _make_mock_cursor([])
        with patch('models.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_get_db.return_value = mock_conn

            from models import get_ride_wind_data
            get_ride_wind_data(77)

        # Verify the SQL was executed with the ride_id
        execute_call = mock_cur.execute.call_args
        assert execute_call is not None
        sql, params = execute_call[0]
        assert 'ride_wind_data' in sql
        assert 'ride_id' in sql
        assert 77 in params

    def test_sql_orders_by_stop_order(self):
        """get_ride_wind_data SQL includes ORDER BY stop_order."""
        mock_cur = _make_mock_cursor([])
        with patch('models.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_get_db.return_value = mock_conn

            from models import get_ride_wind_data
            get_ride_wind_data(42)

        execute_call = mock_cur.execute.call_args
        sql = execute_call[0][0]
        assert 'ORDER BY stop_order' in sql

    def test_returns_multiple_rows_ordered(self):
        """get_ride_wind_data returns all rows (ordering enforced by SQL)."""
        rows = [
            {**SAMPLE_WIND_ROW, 'stop_order': 0},
            {**SAMPLE_WIND_ROW, 'stop_order': 1},
            {**SAMPLE_WIND_ROW, 'stop_order': 2},
        ]
        mock_cur = _make_mock_cursor(rows)
        with patch('models.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_get_db.return_value = mock_conn

            from models import get_ride_wind_data
            result = get_ride_wind_data(42)

        assert len(result) == 3
        assert result[0]['stop_order'] == 0
        assert result[1]['stop_order'] == 1
        assert result[2]['stop_order'] == 2


# ── TestSaveRideWindData ─────────────────────────────────────────────
#
# save_ride_wind_data uses psycopg2.extras.execute_values for a single
# batch INSERT (instead of N individual cur.execute calls).  Tests patch
# execute_values to avoid needing a real DB connection.

class TestSaveRideWindData:

    def _patch_execute_values(self):
        """Return a context manager that patches psycopg2.extras.execute_values."""
        return patch('psycopg2.extras.execute_values')

    def test_inserts_single_row(self):
        """save_ride_wind_data calls execute_values once for a single wind row."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, [SAMPLE_WIND_ROWS_MULTI[0]])

        mock_ev.assert_called_once()

    def test_inserts_multiple_rows(self):
        """save_ride_wind_data calls execute_values once regardless of row count."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, SAMPLE_WIND_ROWS_MULTI)

        # Single batch INSERT regardless of number of rows
        mock_ev.assert_called_once()
        # Values list passed as third arg matches the number of rows
        _, args, _ = mock_ev.mock_calls[0]
        values_list = args[2]
        assert len(values_list) == len(SAMPLE_WIND_ROWS_MULTI)

    def test_sql_contains_on_conflict(self):
        """save_ride_wind_data SQL uses ON CONFLICT for idempotency."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, [SAMPLE_WIND_ROWS_MULTI[0]])

        _, args, _ = mock_ev.mock_calls[0]
        sql = args[1]
        assert 'ON CONFLICT' in sql

    def test_commits_after_inserts(self):
        """save_ride_wind_data calls conn.commit() once after the batch insert."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values():
            from models import save_ride_wind_data
            save_ride_wind_data(42, SAMPLE_WIND_ROWS_MULTI)

        mock_conn.commit.assert_called_once()

    def test_does_not_raise_on_duplicate(self):
        """save_ride_wind_data does not raise even if duplicate rows are passed."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values():
            from models import save_ride_wind_data
            # Should not raise
            save_ride_wind_data(42, [SAMPLE_WIND_ROWS_MULTI[0]])
            save_ride_wind_data(42, [SAMPLE_WIND_ROWS_MULTI[0]])

    def test_data_source_archive_passed_correctly(self):
        """save_ride_wind_data includes data_source='archive' in the values tuple."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        archive_row = {**SAMPLE_WIND_ROWS_MULTI[0], 'data_source': 'archive'}

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, [archive_row])

        _, args, _ = mock_ev.mock_calls[0]
        values_list = args[2]  # third arg is the list of value tuples
        assert len(values_list) == 1
        assert 'archive' in values_list[0]

    def test_data_source_forecast_past_days_passed_correctly(self):
        """save_ride_wind_data includes data_source='forecast_past_days' in values."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        forecast_row = {**SAMPLE_WIND_ROWS_MULTI[0], 'data_source': 'forecast_past_days'}

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, [forecast_row])

        _, args, _ = mock_ev.mock_calls[0]
        values_list = args[2]
        assert 'forecast_past_days' in values_list[0]

    def test_no_op_on_empty_wind_rows(self):
        """save_ride_wind_data does nothing if wind_rows is empty."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, [])

        mock_ev.assert_not_called()
        mock_conn.commit.assert_not_called()

    def test_sql_includes_all_required_columns(self):
        """save_ride_wind_data INSERT SQL includes all required column names."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, [SAMPLE_WIND_ROWS_MULTI[0]])

        _, args, _ = mock_ev.mock_calls[0]
        sql = args[1]
        required_columns = [
            'ride_id', 'stop_order', 'stop_name',
            'wind_speed_kmh', 'wind_direction_deg',
            'headwind_kmh', 'crosswind_kmh',
            'wind_type', 'temperature_c', 'conditions', 'data_source',
        ]
        for col in required_columns:
            assert col in sql, f"Column '{col}' not found in INSERT SQL"

    def test_inserts_ride_id_in_values(self):
        """save_ride_wind_data passes ride_id in the INSERT values tuple."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch('models.get_db', return_value=mock_conn), \
             self._patch_execute_values() as mock_ev:
            from models import save_ride_wind_data
            save_ride_wind_data(42, [SAMPLE_WIND_ROWS_MULTI[0]])

        _, args, _ = mock_ev.mock_calls[0]
        values_list = args[2]
        assert 42 in values_list[0]
