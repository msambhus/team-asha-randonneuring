"""In-progress multi-day events remain followable in the native calendar."""
from datetime import date, datetime, timezone
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
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    with patch('models.club_today', return_value=today), \
         patch('models._utc_now', return_value=now), \
         patch('models._execute', return_value=_Rows(rows)):
        events = models.get_all_upcoming_events.uncached(include_active=True)

    assert [event['id'] for event in events] == [194, 200]
    assert events[0]['is_live'] is True
    assert events[1]['is_live'] is False


def test_mobile_calendar_uses_linked_plan_cutoff_for_active_window():
    today = date(2026, 8, 7)
    execute = _CaptureExecute([_event(194, date(2026, 8, 6), 90)])

    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    with patch('models.club_today', return_value=today), \
         patch('models._utc_now', return_value=now), \
         patch('models._execute', side_effect=execute):
        events = models.get_all_upcoming_events.uncached(include_active=True)

    assert 'COALESCE(ri.time_limit_hours, rp.cutoff_hours) as time_limit_hours' in execute.sql
    assert [event['id'] for event in events] == [194]
    assert events[0]['is_live'] is True


def test_mobile_calendar_marks_same_day_event_live_only_inside_window():
    today = date(2026, 8, 8)
    event = _event(161, today, 20)
    event['start_time'] = '06:00'
    event['timezone'] = 'America/Los_Angeles'

    with patch('models.club_today', return_value=today), \
         patch('models._utc_now', return_value=datetime(
             2026, 8, 8, 20, 0, tzinfo=timezone.utc)), \
         patch('models._execute', return_value=_Rows([event])):
        active = models.get_all_upcoming_events.uncached(include_active=True)

    with patch('models.club_today', return_value=today), \
         patch('models._utc_now', return_value=datetime(
             2026, 8, 8, 12, 0, tzinfo=timezone.utc)), \
         patch('models._execute', return_value=_Rows([event])):
        before_start = models.get_all_upcoming_events.uncached(include_active=True)

    assert active[0]['is_live'] is True
    assert before_start[0]['is_live'] is False


def test_mobile_calendar_excludes_expired_enabled_livetrack_ride():
    today = date(2026, 8, 7)
    active = _event(194, date(2026, 8, 1), None)
    active['has_active_tracking'] = True
    execute = _CaptureExecute([active])

    with patch('models.club_today', return_value=today), \
         patch('models._utc_now', return_value=datetime(
             2026, 8, 7, 12, 0, tzinfo=timezone.utc)), \
         patch('models._execute', side_effect=execute):
        events = models.get_all_upcoming_events.uncached(include_active=True)

    assert 'SELECT active_ride_id FROM rider_live_tracking' in execute.sql
    assert events == []


def test_event_live_window_uses_exact_start_and_cutoff_time():
    event = _event(194, date(2026, 8, 6), 90)
    event['start_time'] = '05:00'
    event['timezone'] = 'America/Chicago'

    assert models._event_is_in_progress(
        event, datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)) is True
    assert models._event_is_in_progress(
        event, datetime(2026, 8, 10, 5, 1, tzinfo=timezone.utc)) is False


def test_garmin_poller_excludes_expired_and_future_ride_assignments():
    rows = [
        dict(_event(194, date(2026, 8, 6), 90), rider_id=7,
             active_ride_id=194, garmin_session_token='live'),
        dict(_event(100, date(2026, 8, 1), 13.5), rider_id=8,
             active_ride_id=100, garmin_session_token='expired'),
        dict(_event(200, date(2026, 8, 9), 13.5), rider_id=9,
             active_ride_id=200, garmin_session_token='future'),
    ]
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    with patch('models._execute', return_value=_Rows(rows)):
        tracked = models.get_enabled_live_tracking(now_utc=now)

    assert [row['rider_id'] for row in tracked] == [7]
