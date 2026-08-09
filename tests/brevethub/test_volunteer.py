"""Tests for volunteer slot signup logic (no DB required)."""
from datetime import date, timedelta
from unittest.mock import patch

from brevethub.services.volunteer import (
    signup_for_slot,
    slot_payload,
    volunteer_open,
)


def _rider(**kwargs):
    base = {'id': 1, 'first_name': 'Alex', 'last_name': 'Mercer', 'email': 'alex@example.com'}
    base.update(kwargs)
    return base


def _event(**kwargs):
    future = (date.today() + timedelta(days=30)).isoformat()
    base = {
        'id': 10,
        'name': 'Test 200k',
        'date': future,
        'volunteer_enabled': True,
    }
    base.update(kwargs)
    return base


def _slot(**kwargs):
    base = {
        'id': 100,
        'event_id': 10,
        'role_name': 'Start Control 07:30-08:30',
        'capacity': 1,
    }
    base.update(kwargs)
    return base


def test_volunteer_open_requires_enabled_and_future_event():
    assert volunteer_open(_event(), slot_count=2) is True
    assert volunteer_open(_event(volunteer_enabled=False), slot_count=2) is False
    assert volunteer_open(_event(date='2000-01-01'), slot_count=2) is False
    assert volunteer_open(_event(), slot_count=0) is False


def test_slot_payload_available_count():
    payload = slot_payload(_slot(), confirmed_count=0)
    assert payload['available'] == 1
    assert payload['full'] is False
    payload_full = slot_payload(_slot(capacity=2), confirmed_count=2)
    assert payload_full['available'] == 0
    assert payload_full['full'] is True


@patch('brevethub.services.volunteer.models.upsert_volunteer_signup')
@patch('brevethub.services.volunteer.models.get_rider_active_volunteer_signups')
@patch('brevethub.services.volunteer.models.count_slot_confirmed_signups')
@patch('brevethub.services.volunteer.models.get_volunteer_signup_for_slot_rider')
@patch('brevethub.services.volunteer.models.get_brevet_event_registration')
@patch('brevethub.services.volunteer.models.get_volunteer_slot')
def test_signup_confirms_first_slot(
    mock_slot, mock_event, mock_existing, mock_count, mock_active, mock_upsert,
):
    mock_slot.return_value = _slot()
    mock_event.return_value = _event()
    mock_existing.return_value = None
    mock_count.return_value = 0
    mock_active.return_value = []
    mock_upsert.return_value = {'id': 1, 'status': 'confirmed'}

    result = signup_for_slot(_rider(), 100)
    assert result['ok'] is True
    assert result['status'] == 'confirmed'
    assert result['needs_approval'] is False
    mock_upsert.assert_called_once()
    assert mock_upsert.call_args.kwargs['status'] == 'confirmed'


@patch('brevethub.services.volunteer.models.upsert_volunteer_signup')
@patch('brevethub.services.volunteer.models.get_rider_active_volunteer_signups')
@patch('brevethub.services.volunteer.models.count_slot_confirmed_signups')
@patch('brevethub.services.volunteer.models.get_volunteer_signup_for_slot_rider')
@patch('brevethub.services.volunteer.models.get_brevet_event_registration')
@patch('brevethub.services.volunteer.models.get_volunteer_slot')
def test_signup_second_slot_is_exception(
    mock_slot, mock_event, mock_existing, mock_count, mock_active, mock_upsert,
):
    mock_slot.return_value = _slot(id=101, role_name='Finish Control')
    mock_event.return_value = _event()
    mock_existing.return_value = None
    mock_count.return_value = 0
    mock_active.return_value = [{'id': 5, 'role_name': 'Start Control'}]
    mock_upsert.return_value = {'id': 2, 'status': 'exception'}

    result = signup_for_slot(_rider(), 101)
    assert result['ok'] is True
    assert result['status'] == 'exception'
    assert result['needs_approval'] is True
    assert mock_upsert.call_args.kwargs['status'] == 'exception'


@patch('brevethub.services.volunteer.models.count_slot_confirmed_signups')
@patch('brevethub.services.volunteer.models.get_volunteer_signup_for_slot_rider')
@patch('brevethub.services.volunteer.models.get_brevet_event_registration')
@patch('brevethub.services.volunteer.models.get_volunteer_slot')
def test_signup_rejects_full_slot(mock_slot, mock_event, mock_existing, mock_count):
    mock_slot.return_value = _slot(capacity=1)
    mock_event.return_value = _event()
    mock_existing.return_value = None
    mock_count.return_value = 1

    result = signup_for_slot(_rider(), 100)
    assert result['ok'] is False
    assert 'full' in result['error'].lower()
