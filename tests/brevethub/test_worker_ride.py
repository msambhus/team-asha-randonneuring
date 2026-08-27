"""Tests for worker ride eligibility and ride-mode validation (no DB)."""
from datetime import date, timedelta
from unittest.mock import patch

from brevethub.services.worker_ride import (
    RIDE_MODE_EVENT_DAY,
    RIDE_MODE_WORKER_RIDE,
    event_week_bounds,
    suggested_ride_mode,
    validate_ride_mode,
    worker_ride_open,
)


def _event(**kwargs):
    future = (date.today() + timedelta(days=30)).isoformat()
    base = {
        'id': 10,
        'name': 'Test 200k',
        'date': future,
        'worker_ride_enabled': True,
    }
    base.update(kwargs)
    return base


def test_event_week_bounds_sunday_to_saturday():
    # 2026-08-27 is a Thursday
    start, end = event_week_bounds(date(2026, 8, 27))
    assert start == date(2026, 8, 23)
    assert end == date(2026, 8, 29)


def test_worker_ride_open_requires_toggle_and_future_week():
    assert worker_ride_open(_event()) is True
    assert worker_ride_open(_event(worker_ride_enabled=False)) is False
    past = (date.today() - timedelta(days=14)).isoformat()
    assert worker_ride_open(_event(date=past)) is False


def test_validate_worker_ride_requires_volunteer():
    event = _event()
    with patch('brevethub.services.worker_ride.rider_is_volunteer', return_value=False):
        result = validate_ride_mode(1, event, RIDE_MODE_WORKER_RIDE)
    assert result['ok'] is False
    assert 'volunteer' in result['error'].lower()


def test_validate_worker_ride_ok_for_volunteer():
    event = _event()
    with patch('brevethub.services.worker_ride.rider_is_volunteer', return_value=True):
        result = validate_ride_mode(1, event, RIDE_MODE_WORKER_RIDE)
    assert result['ok'] is True
    assert result['ride_mode'] == RIDE_MODE_WORKER_RIDE


def test_validate_event_day_default():
    event = _event(worker_ride_enabled=False)
    with patch('brevethub.services.worker_ride.rider_is_volunteer', return_value=False):
        result = validate_ride_mode(1, event, None)
    assert result['ok'] is True
    assert result['ride_mode'] == RIDE_MODE_EVENT_DAY


@patch('brevethub.services.worker_ride.models.get_volunteer_slot')
@patch('brevethub.services.worker_ride.models.get_rider_active_volunteer_signups')
def test_suggested_ride_mode_from_role_flag(mock_signups, mock_slot):
    mock_signups.return_value = [{'slot_id': 100}]
    mock_slot.return_value = {'allows_ride_on_event_day': True}
    assert suggested_ride_mode(1, 10) == RIDE_MODE_EVENT_DAY

    mock_slot.return_value = {'allows_ride_on_event_day': False}
    assert suggested_ride_mode(1, 10) == RIDE_MODE_WORKER_RIDE
