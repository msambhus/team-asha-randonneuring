"""Brevet registration validation and confirmation (no payments)."""
from __future__ import annotations

import re
from datetime import date, datetime

from brevethub import models

PROFILE_REQUIRED_FIELDS = (
    'first_name', 'last_name', 'phone', 'city',
    'emergency_name', 'emergency_phone',
)

_NON_DIGIT = re.compile(r'\D')


def normalize_us_phone(value: str | None) -> str | None:
    """Validate a US phone number and return ``XXX-XXX-XXXX``, or None if invalid.

    Accepts common formats: ``4155550142``, ``415-555-0142``, ``(415) 555-0142``,
    ``+1 415 555 0142``, etc. Requires 10 digits (optional leading country code 1).
    """
    raw = (value or '').strip()
    if not raw:
        return None
    digits = _NON_DIGIT.sub('', raw)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    # Basic NANP rules: area code and exchange cannot start with 0 or 1.
    if digits[0] in '01' or digits[3] in '01':
        return None
    return f'{digits[:3]}-{digits[3:6]}-{digits[6:]}'


def validate_profile_phones(phone, emergency_phone):
    """Normalize required phone fields. Returns (ok, field_errors, normalized)."""
    field_errors = {}
    normalized_phone = normalize_us_phone(phone)
    if not normalized_phone:
        if (phone or '').strip():
            field_errors['phone'] = 'Enter a valid US phone number (10 digits).'
        else:
            field_errors['phone'] = 'Phone is required.'
    normalized_emergency = normalize_us_phone(emergency_phone)
    if not normalized_emergency:
        if (emergency_phone or '').strip():
            field_errors['emergency_phone'] = 'Enter a valid US phone number (10 digits).'
        else:
            field_errors['emergency_phone'] = 'Emergency phone is required.'
    if field_errors:
        return False, field_errors, None
    return True, {}, {
        'phone': normalized_phone,
        'emergency_phone': normalized_emergency,
    }


def normalize_rusa_id(raw: str | None) -> str | None:
    """Shape-only RUSA ID check: digits, 1–7 long. Returns canonical string or None."""
    digits = (raw or '').strip()
    if digits.isdigit() and 1 <= len(digits) <= 7:
        return str(int(digits))
    return None


def _validate_rusa_with_org(rusa_id: str, first_name: str, last_name: str) -> dict:
    from shared.rusa_validator import validate_rusa_id

    return validate_rusa_id(rusa_id, first_name, last_name)


def validate_rusa_profile_fields(
    rusa_id: str | None,
    first_name: str | None,
    last_name: str | None,
) -> tuple[str | None, dict[str, str]]:
    """Validate RUSA ID shape and verify name match against RUSA.org when provided."""
    raw = (rusa_id or '').strip()
    if not raw:
        return None, {}
    normalized = normalize_rusa_id(raw)
    if not normalized:
        return None, {'rusa_id': 'RUSA ID must be numeric (up to 7 digits).'}
    if not (first_name or '').strip() or not (last_name or '').strip():
        return normalized, {
            'rusa_id': 'Enter your first and last name to verify your RUSA ID against RUSA.org.',
        }

    result = _validate_rusa_with_org(
        normalized, first_name.strip(), last_name.strip())
    if not result['valid']:
        return normalized, {
            'rusa_id': result.get('error') or 'RUSA ID could not be verified with RUSA.org.',
        }
    return normalized, {}


def resolve_rusa_id_for_save(
    raw: str | None,
    first_name: str | None,
    last_name: str | None,
) -> tuple[str | None, dict[str, str]]:
    """Normalize and verify RUSA ID for profile save."""
    if not (raw or '').strip():
        return None, {}
    return validate_rusa_profile_fields(raw, first_name, last_name)


def _profile_field_ok(rider: dict, field: str) -> bool:
    if field in ('phone', 'emergency_phone'):
        return normalize_us_phone(rider.get(field)) is not None
    return bool((rider.get(field) or '').strip())


def _current_season_year(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 11 else today.year


def rider_display_name(rider: dict) -> str:
    parts = [rider.get('first_name') or '', rider.get('last_name') or '']
    name = ' '.join(p for p in parts if p).strip()
    if name:
        return name
    email = rider.get('email') or ''
    return email.split('@')[0] if email else 'Rider'


def profile_field_status(rider: dict) -> dict:
    """Return completeness map used by the registration UI."""
    missing = [f for f in PROFILE_REQUIRED_FIELDS if not _profile_field_ok(rider, f)]
    return {
        'complete': not missing and bool(rider.get('club_id')),
        'missing': missing,
        'has_rusa': bool(rider.get('rusa_id')),
        'rusa_duplicate': bool(rider.get('rusa_id_duplicate')),
        'sfr_member_current': (
            rider.get('sfr_member_year') is not None and
            int(rider['sfr_member_year']) >= _current_season_year()
        ),
    }


def membership_pills(rider: dict | None) -> list[dict]:
    """Hero status pills for the calendar/register surfaces."""
    if not rider:
        return []
    status = profile_field_status(rider)
    year = _current_season_year()
    pills = []
    if rider.get('rusa_id'):
        pills.append({
            'label': f"RUSA #{rider['rusa_id']}",
            'status': 'Active' if not status['rusa_duplicate'] else 'Needs review',
            'ok': not status['rusa_duplicate'],
        })
    else:
        pills.append({'label': 'RUSA #', 'status': 'Not provided', 'ok': False})
    if status['sfr_member_current']:
        pills.append({'label': 'SFR Membership', 'status': f'Active · {year}', 'ok': True})
    else:
        pills.append({'label': 'SFR Membership', 'status': f'Not current · {year}', 'ok': False})
    pills.append({
        'label': 'Profile',
        'status': 'Complete' if status['complete'] else 'Incomplete',
        'ok': status['complete'],
    })
    return pills


def registration_open(event: dict, *, confirmed_count: int) -> bool:
    if not event.get('registration_enabled'):
        return False
    deadline = event.get('registration_deadline')
    if deadline:
        try:
            if date.fromisoformat(str(deadline)[:10]) < date.today():
                return False
        except ValueError:
            pass
    capacity = event.get('capacity')
    if capacity is not None and confirmed_count >= int(capacity):
        return False
    return True


def evaluate_registration(rider: dict, event: dict, *, confirmed_count: int) -> dict:
    """Return {ok, blockers, exceptions, registration_status}."""
    blockers: list[str] = []
    exceptions: list[str] = []
    status = profile_field_status(rider)

    if not status['complete']:
        blockers.append('Complete your profile before registering.')
    if not event.get('registration_enabled'):
        blockers.append('Online registration is not open for this event.')
    if not registration_open(event, confirmed_count=confirmed_count):
        deadline = event.get('registration_deadline')
        capacity = event.get('capacity')
        if deadline:
            try:
                if date.fromisoformat(str(deadline)[:10]) < date.today():
                    blockers.append('Registration deadline has passed.')
            except ValueError:
                pass
        if capacity is not None and confirmed_count >= int(capacity):
            blockers.append('This event is full.')

    if not rider.get('rusa_id'):
        exceptions.append('RUSA number not on file.')
    elif status['rusa_duplicate']:
        exceptions.append('RUSA number is also claimed by another account.')
    elif status['complete']:
        _, rusa_errors = validate_rusa_profile_fields(
            rider.get('rusa_id'),
            rider.get('first_name'),
            rider.get('last_name'),
        )
        if rusa_errors.get('rusa_id'):
            blockers.append(rusa_errors['rusa_id'])
    if not status['sfr_member_current']:
        exceptions.append('SFR membership is not marked current for this season.')

    reg_status = 'confirmed'
    if blockers:
        reg_status = None
    elif exceptions:
        reg_status = 'exception'
    elif event.get('capacity') is not None and confirmed_count >= int(event['capacity']):
        reg_status = 'waitlist'

    return {
        'ok': not blockers,
        'blockers': blockers,
        'exceptions': exceptions,
        'registration_status': reg_status,
    }


def confirmation_code(event_id: int, rider_id: int) -> str:
    seed = event_id * 349 + rider_id + 4000
    return f'SFR-{date.today().year}-{seed % 10000:04d}'


def profile_snapshot(rider: dict) -> dict:
    return {
        'first_name': rider.get('first_name'),
        'last_name': rider.get('last_name'),
        'email': rider.get('email'),
        'phone': rider.get('phone'),
        'city': rider.get('city'),
        'rusa_id': rider.get('rusa_id'),
        'emergency_name': rider.get('emergency_name'),
        'emergency_phone': rider.get('emergency_phone'),
        'sfr_member_year': rider.get('sfr_member_year'),
        'captured_at': datetime.utcnow().isoformat() + 'Z',
    }


def profile_payload(rider: dict, *, edit_url: str) -> dict:
    """JSON profile shape shared by single and bulk registration flows."""
    return {
        'name': rider_display_name(rider),
        'first_name': rider.get('first_name'),
        'last_name': rider.get('last_name'),
        'email': rider.get('email'),
        'phone': rider.get('phone'),
        'city': rider.get('city'),
        'rusa_id': rider.get('rusa_id'),
        'sfr_member_year': rider.get('sfr_member_year'),
        'emergency_name': rider.get('emergency_name'),
        'emergency_phone': rider.get('emergency_phone'),
        'edit_url': edit_url,
    }


def status_display_label(status: str | None) -> str:
    """User-facing ride status label."""
    st = (status or '').strip().lower()
    if not st:
        return '—'
    if st == 'registered':
        return 'Registered'
    return st.replace('_', ' ').title()


def progress_label(*, event_past: bool, status: str, registration_status: str | None) -> str:
    """Human-readable rider progress for admin roster display."""
    st = (status or '').lower()
    reg = (registration_status or '').lower()
    if st == 'withdrawal_requested':
        return 'Withdrawal pending'
    if reg == 'exception':
        return 'Needs review'
    if reg == 'waitlist':
        return 'Waitlist'
    if not event_past:
        if st == 'registered' and reg == 'confirmed':
            return 'Registered'
        if st == 'interested' and reg:
            return reg.replace('_', ' ').title()
        if st == 'interested':
            return 'Interested'
        if st == 'withdraw':
            return 'Withdrawn'
        return status_display_label(st)
    if st == 'finished':
        return 'Finished'
    if st in ('dnf', 'dns', 'otl'):
        return st.upper()
    if st == 'registered':
        return 'Awaiting result'
    return status_display_label(st)


def confirm_registration_for_event(rider: dict, event: dict, *, waiver,
                                   waiver_accepted: bool) -> dict:
    """Shared single-event confirm logic for individual and bulk registration."""
    if not waiver_accepted or not waiver:
        return {'ok': False, 'error': 'Waiver acceptance is required.'}
    confirmed = models.get_event_registration_count(event['id'])
    evaluation = evaluate_registration(rider, event, confirmed_count=confirmed)
    if not evaluation['ok']:
        return {
            'ok': False,
            'error': evaluation['blockers'][0],
            'blockers': evaluation['blockers'],
        }
    snap = profile_snapshot(rider)
    models.record_waiver_acceptance(
        event['id'], rider['id'], waiver['id'], snap)
    reg_status = evaluation['registration_status'] or 'confirmed'
    exc_reason = '; '.join(evaluation['exceptions']) if evaluation['exceptions'] else None
    code = confirmation_code(event['id'], rider['id'])
    row = models.confirm_event_registration(
        rider['id'], event['id'],
        registration_status=reg_status,
        exception_reason=exc_reason,
        confirmation_code=code,
    )
    if row is None:
        return {'ok': False, 'error': 'Cannot register — ride already has a posted result.'}
    return {
        'ok': True,
        'event_id': event['id'],
        'registration_status': reg_status,
        'confirmation_code': row.get('confirmation_code') or code,
        'exceptions': evaluation['exceptions'],
        'event': {
            'name': event['name'],
            'date': str(event['date']),
            'distance_km': event['distance_km'],
        },
    }
