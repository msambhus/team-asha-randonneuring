"""Admin event metadata edit helpers."""
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from brevethub.routes.admin import (
    _apply_rwgps_elevation,
    _apply_route_elevation_fallback,
    _parse_event_edit_form,
)
from brevethub.routes.calendar import _fill_scraped_event_elevations


def test_parse_event_edit_form_maps_registration_fields():
    form = MultiDict([
        ('start_location', "Peet's, Mill Valley"),
        ('start_time', '07:00'),
        ('time_limit_hours', '13.5'),
        ('rwgps_url', 'https://ridewithgps.com/routes/123'),
        ('fee_dollars', '35'),
        ('registration_deadline', '2026-08-01'),
        ('capacity', '120'),
        ('event_summary', 'Bag drop at start.'),
        ('registration_enabled', 'on'),
        ('volunteer_enabled', 'on'),
        ('club_id', '3'),
    ])
    fields = _parse_event_edit_form(form)
    assert fields['start_location'] == "Peet's, Mill Valley"
    assert fields['start_time'] == '07:00'
    assert fields['time_limit_hours'] == 13.5
    assert fields['rwgps_url'] == 'https://ridewithgps.com/routes/123'
    assert 'elevation_ft' not in fields
    assert fields['fee_cents'] == 3500
    assert fields['registration_deadline'] == '2026-08-01'
    assert fields['capacity'] == 120
    assert fields['event_summary'] == 'Bag drop at start.'
    assert fields['registration_enabled'] is True
    assert fields['volunteer_enabled'] is True
    assert fields['club_id'] == 3


def test_parse_event_edit_form_clears_optional_fields():
    form = MultiDict([
        ('start_location', ''),
        ('start_time', ''),
        ('fee_dollars', ''),
        ('capacity', ''),
        ('club_id', ''),
    ])
    fields = _parse_event_edit_form(form)
    assert fields['start_location'] is None
    assert fields['start_time'] is None
    assert fields['fee_cents'] is None
    assert fields['capacity'] is None
    assert fields['club_id'] is None
    assert fields['registration_enabled'] is False


def test_apply_rwgps_elevation_fetches_when_url_changes():
    fields = {'rwgps_url': 'https://ridewithgps.com/routes/999/'}
    with patch('brevethub.routes.admin._fetch_rwgps_elevation_ft', return_value=8500):
        updated, note = _apply_rwgps_elevation(
            fields,
            previous_rwgps_url='https://ridewithgps.com/routes/123',
        )
    assert updated['rwgps_url'] == 'https://ridewithgps.com/routes/999'
    assert updated['elevation_ft'] == 8500
    assert '8,500 ft' in note


def test_apply_rwgps_elevation_skips_fetch_when_url_unchanged_and_elevation_present():
    url = 'https://ridewithgps.com/routes/123'
    fields = {'rwgps_url': url}
    with patch('brevethub.routes.admin._fetch_rwgps_elevation_ft') as mock_fetch:
        updated, note = _apply_rwgps_elevation(
            fields,
            previous_rwgps_url=url,
            previous_elevation_ft=8500,
        )
    mock_fetch.assert_not_called()
    assert 'elevation_ft' not in updated
    assert note is None


def test_apply_rwgps_elevation_fetches_when_url_unchanged_but_elevation_missing():
    url = 'https://ridewithgps.com/routes/123'
    fields = {'rwgps_url': url}
    with patch('brevethub.routes.admin._fetch_rwgps_elevation_ft', return_value=8500):
        updated, note = _apply_rwgps_elevation(
            fields,
            previous_rwgps_url=url,
            previous_elevation_ft=None,
        )
    assert updated['elevation_ft'] == 8500
    assert '8,500 ft' in note


def test_apply_rwgps_elevation_clears_when_url_removed():
    fields = {'rwgps_url': ''}
    updated, note = _apply_rwgps_elevation(
        fields,
        previous_rwgps_url='https://ridewithgps.com/routes/123',
    )
    assert updated['rwgps_url'] is None
    assert updated['elevation_ft'] is None
    assert 'cleared' in note.lower()


def test_fill_scraped_event_elevations_uses_route_sibling():
    events = [
        {'route_id': '2416', 'name': 'Laguna Lake', 'date': '2026-05-03', 'elevation_ft': 6654},
        {'route_id': '2416', 'name': 'Laguna Lake', 'date': '2026-08-29', 'elevation_ft': None},
    ]
    _fill_scraped_event_elevations(events)
    assert events[1]['elevation_ft'] == 6654


def test_apply_route_elevation_fallback_uses_rusa_route_id():
    fields = {'start_location': 'Start'}
    event = {'rusa_route_id': '2416', 'elevation_ft': None}
    with patch('brevethub.routes.admin.models.get_cached_elevation_for_rusa_route', return_value=6654):
        updated, note = _apply_route_elevation_fallback(fields, event)
    assert updated['elevation_ft'] == 6654
    assert '6,654 ft' in note
