"""Tests for brevet registration validation (no DB required)."""
from datetime import date
from unittest.mock import patch

from brevethub.services.registration import (
    evaluate_registration,
    membership_season_year,
    normalize_rusa_id,
    normalize_us_phone,
    profile_field_status,
    registration_open,
    resolve_rusa_id_for_save,
    rusa_membership_needs_refresh,
    refresh_rusa_membership,
    rusa_status_from_rider_row,
    sfr_membership_current,
    validate_profile_phones,
    validate_rusa_profile_fields,
)


def _current_rusa_lookup(**overrides):
    base = {
        'found': True,
        'rusa_id': '12847',
        'membership_expires': '2026-12-31',
        'current': True,
        'error': None,
        'from_cache': True,
    }
    base.update(overrides)
    return base


def _membership_bundle(**rusa_overrides):
    rusa = _current_rusa_lookup(**rusa_overrides)
    return {
        'season_year': 2026,
        'rusa': {
            **rusa,
            'renew_url': 'https://rusa.org/pages/join-renew-membership',
            'expired': not rusa['current'],
        },
        'sfr': {
            'member_year': 2026,
            'current': True,
            'expired': False,
            'renew_url': 'https://sfrandonneurs.org/',
        },
    }


def _rider(**kwargs):
    base = {
        'id': 1,
        'first_name': 'Alex',
        'last_name': 'Mercer',
        'phone': '415-555-0142',
        'city': 'San Francisco, CA',
        'emergency_name': 'Jordan Mercer',
        'emergency_phone': '415-555-0199',
        'club_id': 3,
        'rusa_id': '12847',
        'sfr_member_year': 2026,
        'rusa_id_duplicate': False,
    }
    base.update(kwargs)
    return base


def _event(**kwargs):
    base = {
        'registration_enabled': True,
        'registration_deadline': '2099-12-31',
        'capacity': 50,
    }
    base.update(kwargs)
    return base


def _mock_rusa_valid(mock_validate):
    mock_validate.return_value = {
        'valid': True,
        'rusa_name': 'Mercer, Alex',
        'rusa_club': 'SFR',
        'error': None,
    }


def _mock_refresh_rusa(mock_refresh, rider=None):
    r = rider or _rider()
    mock_refresh.return_value = (r, _current_rusa_lookup(from_cache=False))


def test_profile_complete_when_all_fields_present():
    status = profile_field_status(_rider(), rusa_lookup=_current_rusa_lookup())
    assert status['complete'] is True
    assert status['sfr_member_current'] is True
    assert status['rusa_membership_current'] is True


def test_membership_season_year_calendar_year():
    assert membership_season_year(date(2026, 1, 1)) == 2026
    assert membership_season_year(date(2026, 12, 31)) == 2026
    assert membership_season_year(date(2026, 8, 9)) == 2026


def test_sfr_membership_valid_through_dec_31():
    rider = _rider(sfr_member_year=2026)
    assert sfr_membership_current(rider, date(2026, 12, 31)) is True
    assert sfr_membership_current(rider, date(2026, 8, 9)) is True
    assert sfr_membership_current(rider, date(2027, 1, 1)) is False


def test_sfr_membership_expired_when_year_behind():
    rider = _rider(sfr_member_year=2025)
    assert sfr_membership_current(rider, date(2026, 8, 9)) is False


def test_profile_incomplete_lists_missing_fields():
    status = profile_field_status(_rider(first_name='', club_id=None))
    assert status['complete'] is False
    assert 'first_name' in status['missing']


def test_registration_open_respects_deadline_and_capacity():
    assert registration_open(_event(), confirmed_count=10) is True
    assert registration_open(_event(registration_deadline='2000-01-01'), confirmed_count=0) is False
    assert registration_open(_event(capacity=5), confirmed_count=5) is False


@patch('brevethub.services.registration.refresh_rusa_membership')
@patch('brevethub.services.registration.membership_status')
@patch('brevethub.services.registration._validate_rusa_with_org')
def test_evaluate_registration_confirms_clean_rider(mock_validate, mock_member_status, mock_refresh):
    _mock_rusa_valid(mock_validate)
    _mock_refresh_rusa(mock_refresh)
    mock_member_status.return_value = _membership_bundle()
    result = evaluate_registration(_rider(), _event(), confirmed_count=0)
    assert result['ok'] is True
    assert result['registration_status'] == 'confirmed'
    assert result['exceptions'] == []


@patch('brevethub.services.registration.refresh_rusa_membership')
@patch('brevethub.services.registration.membership_status')
@patch('brevethub.services.registration._validate_rusa_with_org')
def test_evaluate_registration_blocks_expired_sfr_membership(mock_validate, mock_member_status, mock_refresh):
    _mock_rusa_valid(mock_validate)
    rider = _rider(sfr_member_year=2020)
    _mock_refresh_rusa(mock_refresh, rider)
    mock_member_status.return_value = _membership_bundle()
    mock_member_status.return_value['sfr']['expired'] = True
    mock_member_status.return_value['sfr']['current'] = False
    mock_member_status.return_value['sfr']['member_year'] = 2020
    result = evaluate_registration(
        rider,
        _event(),
        confirmed_count=0,
    )
    assert result['ok'] is False
    assert result['sfr_membership_expired'] is True
    assert result['registration_status'] is None
    assert any('SFR' in b for b in result['blockers'])


@patch('brevethub.services.registration.refresh_rusa_membership')
@patch('brevethub.services.registration.membership_status')
@patch('brevethub.services.registration._validate_rusa_with_org')
def test_evaluate_registration_blocks_expired_rusa_membership(mock_validate, mock_member_status, mock_refresh):
    _mock_rusa_valid(mock_validate)
    _mock_refresh_rusa(mock_refresh)
    bundle = _membership_bundle(current=False, membership_expires='2025-12-31')
    bundle['rusa']['expired'] = True
    mock_member_status.return_value = bundle
    result = evaluate_registration(_rider(), _event(), confirmed_count=0)
    assert result['ok'] is False
    assert result['rusa_membership_expired'] is True
    assert any('RUSA' in b for b in result['blockers'])


@patch('brevethub.services.registration.refresh_rusa_membership')
@patch('brevethub.services.registration.membership_status')
def test_evaluate_registration_flags_rusa_missing_as_exception(mock_member_status, mock_refresh):
    _mock_refresh_rusa(mock_refresh, _rider(rusa_id=None, sfr_member_year=2026))
    mock_member_status.return_value = _membership_bundle()
    result = evaluate_registration(
        _rider(rusa_id=None, sfr_member_year=2026),
        _event(),
        confirmed_count=0,
    )
    assert result['ok'] is True
    assert result['registration_status'] == 'exception'
    assert result['exceptions']


@patch('brevethub.services.registration.refresh_rusa_membership')
@patch('brevethub.services.registration.membership_status')
def test_evaluate_registration_blocks_incomplete_profile(mock_member_status, mock_refresh):
    _mock_refresh_rusa(mock_refresh, _rider(phone=''))
    mock_member_status.return_value = _membership_bundle()
    result = evaluate_registration(_rider(phone=''), _event(), confirmed_count=0)
    assert result['ok'] is False
    assert result['registration_status'] is None


@patch('brevethub.services.registration.refresh_rusa_membership')
@patch('brevethub.services.registration.membership_status')
@patch('brevethub.services.registration._validate_rusa_with_org')
def test_evaluate_registration_blocks_invalid_rusa(mock_validate, mock_member_status, mock_refresh):
    _mock_refresh_rusa(mock_refresh)
    mock_member_status.return_value = _membership_bundle()
    mock_validate.return_value = {
        'valid': False,
        'error': 'Name mismatch. RUSA record shows: Smith, Alex',
        'rusa_name': 'Smith, Alex',
        'rusa_club': 'SFR',
    }
    result = evaluate_registration(_rider(), _event(), confirmed_count=0)
    assert result['ok'] is False
    assert 'Name mismatch' in result['blockers'][0]
    assert result['registration_status'] is None


def test_progress_label_registered_and_results():
    from brevethub.services.registration import progress_label
    assert progress_label(event_past=False, status='registered', registration_status='confirmed') == 'Registered'
    assert progress_label(event_past=True, status='dnf', registration_status='confirmed') == 'DNF'
    assert progress_label(event_past=False, status='registered', registration_status='exception') == 'Needs review'


def test_normalize_us_phone_accepts_common_formats():
    assert normalize_us_phone('4155550142') == '415-555-0142'
    assert normalize_us_phone('415-555-0142') == '415-555-0142'
    assert normalize_us_phone('(415) 555-0142') == '415-555-0142'
    assert normalize_us_phone('+1 415 555 0142') == '415-555-0142'
    assert normalize_us_phone('1-415-555-0142') == '415-555-0142'


def test_normalize_us_phone_rejects_invalid_numbers():
    assert normalize_us_phone('12345') is None
    assert normalize_us_phone('415-555-014') is None
    assert normalize_us_phone('015-555-0142') is None
    assert normalize_us_phone('415-055-0142') is None
    assert normalize_us_phone('not-a-phone') is None


def test_validate_profile_phones_normalizes_both_fields():
    ok, field_errors, phones = validate_profile_phones('(415) 555-0142', '4155550199')
    assert ok is True
    assert field_errors == {}
    assert phones == {'phone': '415-555-0142', 'emergency_phone': '415-555-0199'}


def test_validate_profile_phones_returns_field_errors():
    ok, field_errors, phones = validate_profile_phones('123', '')
    assert ok is False
    assert phones is None
    assert field_errors['phone']
    assert field_errors['emergency_phone']


def test_profile_incomplete_when_phone_format_invalid():
    status = profile_field_status(_rider(phone='12345'), rusa_lookup=_current_rusa_lookup())
    assert status['complete'] is False
    assert 'phone' in status['missing']


def test_normalize_rusa_id_strips_leading_zeros():
    assert normalize_rusa_id('0012847') == '12847'
    assert normalize_rusa_id('abc') is None
    assert normalize_rusa_id('12345678') is None


def test_validate_rusa_profile_fields_requires_names():
    normalized, errors = validate_rusa_profile_fields('12847', '', 'Mercer')
    assert normalized == '12847'
    assert 'first and last name' in errors['rusa_id'].lower()


@patch('brevethub.services.registration._validate_rusa_with_org')
def test_validate_rusa_profile_fields_verifies_with_rusa_org(mock_validate):
    _mock_rusa_valid(mock_validate)
    normalized, errors = validate_rusa_profile_fields('12847', 'Alex', 'Mercer')
    assert normalized == '12847'
    assert errors == {}
    mock_validate.assert_called_once_with('12847', 'Alex', 'Mercer')


def test_resolve_rusa_id_for_save_skips_empty():
    normalized, errors = resolve_rusa_id_for_save('', 'Alex', 'Mercer')
    assert normalized is None
    assert errors == {}


def test_rusa_membership_needs_refresh_when_expired_or_missing():
    assert rusa_membership_needs_refresh(
        _rider(rusa_membership_expires=date(2025, 12, 31)), date(2026, 8, 9))
    assert rusa_membership_needs_refresh(_rider(
        rusa_membership_checked_at=date(2026, 1, 1), rusa_membership_expires=None))
    assert not rusa_membership_needs_refresh(
        _rider(
            rusa_membership_expires=date(2026, 12, 31),
            rusa_membership_checked_at=date(2026, 1, 1),
        ),
        date(2026, 8, 9),
    )


def test_rusa_status_from_rider_row_uses_db_cache():
    status = rusa_status_from_rider_row(
        _rider(
            rusa_membership_expires=date(2026, 12, 31),
            rusa_membership_checked_at=date(2026, 1, 1),
        ),
        date(2026, 8, 9),
    )
    assert status['found'] is True
    assert status['current'] is True
    assert status['membership_expires'] == '2026-12-31'
    assert status['from_cache'] is True


@patch('brevethub.services.registration.models.update_rider_rusa_membership')
@patch('brevethub.services.registration.lookup_rusa_membership')
def test_refresh_rusa_membership_persists_new_expiry(mock_lookup, mock_update):
    mock_lookup.return_value = _current_rusa_lookup(from_cache=False)
    mock_update.return_value = _rider(
        rusa_membership_expires=date(2026, 12, 31),
        rusa_membership_checked_at=date(2026, 8, 9),
    )
    rider, status = refresh_rusa_membership(
        _rider(rusa_membership_expires=date(2025, 12, 31)), force=True)
    mock_update.assert_called_once()
    assert status['membership_expires'] == '2026-12-31'
