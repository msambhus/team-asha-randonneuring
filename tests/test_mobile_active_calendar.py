"""In-progress multi-day events remain followable in the native calendar."""
from datetime import date
from unittest.mock import patch

import models


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _CaptureExecute:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None

    def __call__(self, sql, _params):
        self.sql = sql
        return _Rows(self.rows)


def _event(event_id, event_date, limit_hours):
    return {
        'id': event_id, 'date': event_date, 'name': f'Ride {event_id}',
        'route_name': f'Ride {event_id}', 'distance_km': 1200,
        'time_limit_hours': limit_hours,
    }


def test_mobile_calendar_keeps_active_multiday_event_but_not_expired_ride():
    today = date(2026, 8, 7)
    rows = [
        _event(194, date(2026, 8, 6), 90),   # Coulee is still in progress
        _event(100, date(2026, 8, 5), 13.5), # short event has expired
        _event(200, date(2026, 8, 8), 13.5), # future event
    ]
    with patch('models.club_today', return_value=today), \
         patch('models._execute', return_value=_Rows(rows)):
        events = models.get_all_upcoming_events.uncached(include_active=True)

    assert [event['id'] for event in events] == [194, 200]
    assert events[0]['is_live'] is True
    assert events[1]['is_live'] is False


def test_mobile_calendar_uses_linked_plan_cutoff_for_active_window():
    today = date(2026, 8, 7)
    execute = _CaptureExecute([_event(194, date(2026, 8, 6), 90)])

    with patch('models.club_today', return_value=today), \
         patch('models._execute', side_effect=execute):
        events = models.get_all_upcoming_events.uncached(include_active=True)

    assert 'COALESCE(ri.time_limit_hours, rp.cutoff_hours) as time_limit_hours' in execute.sql
    assert [event['id'] for event in events] == [194]
    assert events[0]['is_live'] is True
