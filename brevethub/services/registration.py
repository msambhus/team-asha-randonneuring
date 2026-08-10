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


# Membership: RUSA (national, scraped expiry) and SFR (club, self-reported year).
# Both follow the calendar year (Jan 1 – Dec 31).
RUSA_RENEW_URL = 'https://rusa.org/pages/join-renew-membership'
SFR_RENEW_URL = 'https://sfrandonneurs.org/'
RUSA_MEMBERSHIP_EXPIRED_BLOCKER = (
    'RUSA membership is expired. Renew at RUSA.org before registering for rides.'
)
SFR_MEMBERSHIP_EXPIRED_BLOCKER = (
    'SFR club membership is expired. Renew with SFR before registering for rides.'
)


def membership_season_year(today: date | None = None) -> int:
    """Calendar year label for membership checks."""
    return (today or date.today()).year


def sfr_membership_current(rider: dict, today: date | None = None) -> bool:
    """True when the rider's SFR club membership year covers the current calendar year."""
    member_year = rider.get('sfr_member_year')
    if member_year is None:
        return False
    return int(member_year) >= membership_season_year(today)


def sfr_membership_expired(rider: dict, today: date | None = None) -> bool:
    return not sfr_membership_current(rider, today)


def _coerce_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if hasattr(value, 'date') and callable(value.date):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def lookup_rusa_membership(rusa_id: str | None, today: date | None = None) -> dict:
    """Scrape RUSA.org member search for expiry (authoritative RUSA membership)."""
    from shared.rusa_validator import get_rusa_membership_status

    normalized = normalize_rusa_id(rusa_id)
    if not normalized:
        return {
            'found': False,
            'rusa_id': None,
            'rusa_name': None,
            'city': None,
            'rusa_club': None,
            'membership_expires': None,
            'current': False,
            'error': 'RUSA ID not on file.',
            'from_cache': False,
        }
    result = get_rusa_membership_status(normalized, today=today)
    result['from_cache'] = False
    return result


def rusa_membership_needs_refresh(rider: dict, today: date | None = None) -> bool:
    """True when DB cache is missing, not found, or past expiry — time to re-scrape."""
    if not rider.get('rusa_id'):
        return False
    if rider.get('rusa_membership_checked_at') is None:
        return True
    expires = _coerce_date(rider.get('rusa_membership_expires'))
    if expires is None:
        return True
    return expires < (today or date.today())


def rusa_status_from_rider_row(rider: dict, today: date | None = None) -> dict:
    """Build RUSA membership status from cached DB columns."""
    today = today or date.today()
    rusa_id = rider.get('rusa_id')
    if not rusa_id:
        return lookup_rusa_membership(None, today=today)

    expires = _coerce_date(rider.get('rusa_membership_expires'))
    checked = rider.get('rusa_membership_checked_at')
    if checked is None:
        return {
            'found': False,
            'rusa_id': str(rusa_id),
            'rusa_name': None,
            'city': None,
            'rusa_club': None,
            'membership_expires': None,
            'current': False,
            'checked_at': None,
            'from_cache': True,
            'error': 'RUSA membership has not been checked yet.',
        }

    found = expires is not None
    current = found and expires >= today
    checked_iso = checked.isoformat() if hasattr(checked, 'isoformat') else str(checked)
    return {
        'found': found,
        'rusa_id': str(rusa_id),
        'rusa_name': None,
        'city': None,
        'rusa_club': None,
        'membership_expires': expires.isoformat() if expires else None,
        'current': current,
        'checked_at': checked_iso,
        'from_cache': True,
        'error': None if found else f'RUSA ID {rusa_id} not found on RUSA.org',
    }


def _persist_rusa_membership(rider_id: int, scraped: dict) -> dict | None:
    expires = _coerce_date(scraped.get('membership_expires'))
    return models.update_rider_rusa_membership(rider_id, membership_expires=expires)


def refresh_rusa_membership(rider: dict, *, force: bool = False,
                            today: date | None = None) -> tuple[dict, dict]:
    """Re-scrape RUSA.org when needed and persist expiry to ``rp_rider``.

    When ``force`` is True (registration flow), always scrape and update the DB
    when the expiry date changes.
    """
    today = today or date.today()
    rider_id = rider.get('id')
    if not rider.get('rusa_id'):
        return rider, lookup_rusa_membership(None, today=today)

    if not force and not rusa_membership_needs_refresh(rider, today):
        return rider, rusa_status_from_rider_row(rider, today=today)

    scraped = lookup_rusa_membership(rider['rusa_id'], today=today)
    if rider_id:
        previous = _coerce_date(rider.get('rusa_membership_expires'))
        new_expires = _coerce_date(scraped.get('membership_expires'))
        if force or previous != new_expires or rider.get('rusa_membership_checked_at') is None:
            updated = _persist_rusa_membership(rider_id, scraped)
            if updated:
                rider = updated
    scraped['from_cache'] = False
    if rider.get('rusa_membership_checked_at'):
        checked = rider['rusa_membership_checked_at']
        scraped['checked_at'] = checked.isoformat() if hasattr(checked, 'isoformat') else str(checked)
    return rider, scraped


def sync_rusa_membership_by_rusa_id(rusa_id: str | None, *, rider_id: int | None = None,
                                    today: date | None = None) -> dict:
    """Scrape RUSA.org for a member number and persist on the matching rider row."""
    today = today or date.today()
    scraped = lookup_rusa_membership(rusa_id, today=today)
    target_id = rider_id
    if not target_id and scraped.get('rusa_id'):
        row = models.get_rider_by_rusa_id(scraped['rusa_id'])
        target_id = row['id'] if row else None
    if target_id:
        _persist_rusa_membership(target_id, scraped)
    return scraped


def rusa_membership_status_for_rider(rider: dict, today: date | None = None,
                                     *, force: bool = False) -> dict:
    """RUSA membership for a rider, using DB cache with conditional refresh."""
    _, status = refresh_rusa_membership(rider, force=force, today=today)
    return status


def membership_status(rider: dict, today: date | None = None,
                      *, refresh_rusa: bool = False) -> dict:
    """Combined RUSA + SFR membership status for APIs and registration UI."""
    today = today or date.today()
    year = membership_season_year(today)
    rider, rusa = refresh_rusa_membership(
        rider,
        force=refresh_rusa,
        today=today,
    )
    sfr_current = sfr_membership_current(rider, today=today)
    return {
        'season_year': year,
        'rusa': {
            **rusa,
            'renew_url': RUSA_RENEW_URL,
            'expired': bool(rider.get('rusa_id')) and not rusa.get('current'),
        },
        'sfr': {
            'member_year': rider.get('sfr_member_year'),
            'current': sfr_current,
            'expired': sfr_membership_expired(rider, today=today),
            'renew_url': SFR_RENEW_URL,
        },
    }


# Back-compat aliases used by older call sites during transition.
def rusa_membership_season_year(today: date | None = None) -> int:
    return membership_season_year(today)


def membership_expired(rider: dict, today: date | None = None) -> bool:
    """True when either RUSA (scraped) or SFR (self-reported) membership is expired."""
    status = membership_status(rider, today=today)
    rusa_expired = bool(rider.get('rusa_id')) and status['rusa']['expired']
    return rusa_expired or status['sfr']['expired']


def _format_name_part(value: str) -> str:
    """Title-case one name fragment (handles dots/underscores in email locals)."""
    value = (value or '').strip()
    if not value:
        return ''
    tokens = re.split(r'[._\-]+', value)
    formatted = []
    for token in tokens:
        if not token:
            continue
        name = token.title()
        name = re.sub(r'\bMc([a-z])', lambda m: f"Mc{m.group(1).upper()}", name)
        name = re.sub(r'\bMac([a-z])', lambda m: f"Mac{m.group(1).upper()}", name)
        name = re.sub(r"\bO'([a-z])", lambda m: f"O'{m.group(1).upper()}", name)
        formatted.append(name)
    return ' '.join(formatted)


def rider_display_name(rider: dict) -> str:
    parts = [
        _format_name_part(rider.get('first_name') or ''),
        _format_name_part(rider.get('last_name') or ''),
    ]
    name = ' '.join(p for p in parts if p).strip()
    if name:
        return name
    email = rider.get('email') or ''
    local = email.split('@')[0] if email else ''
    if local:
        return _format_name_part(local)
    return 'Rider'


def profile_field_status(rider: dict, *, rusa_lookup: dict | None = None) -> dict:
    """Return completeness map used by the registration UI."""
    missing = [f for f in PROFILE_REQUIRED_FIELDS if not _profile_field_ok(rider, f)]
    rusa = rusa_lookup or rusa_status_from_rider_row(rider)
    sfr_current = sfr_membership_current(rider)
    rusa_expired = bool(rider.get('rusa_id')) and not rusa.get('current')
    sfr_expired = sfr_membership_expired(rider)
    return {
        'complete': not missing and bool(rider.get('club_id')),
        'missing': missing,
        'has_rusa': bool(rider.get('rusa_id')),
        'rusa_duplicate': bool(rider.get('rusa_id_duplicate')),
        'rusa_membership_current': bool(rider.get('rusa_id')) and rusa.get('current'),
        'rusa_membership_expires': rusa.get('membership_expires'),
        'rusa_membership_expired': rusa_expired,
        'sfr_member_current': sfr_current,
        'sfr_membership_expired': sfr_expired,
        'membership_expired': rusa_expired or sfr_expired,
    }


def membership_pills(rider: dict | None, *, membership: dict | None = None) -> list[dict]:
    """Hero status pills for the calendar/register surfaces."""
    if not rider:
        return []
    status = profile_field_status(
        rider,
        rusa_lookup=(membership or {}).get('rusa'),
    ) if membership else profile_field_status(rider)
    year = membership_season_year()
    pills = []
    if rider.get('rusa_id'):
        pills.append({
            'label': 'RUSA #',
            'status': str(rider['rusa_id']),
            'ok': not status['rusa_duplicate'],
        })
    else:
        pills.append({'label': 'RUSA #', 'status': 'Not provided', 'ok': False})
    if status['rusa_membership_current']:
        expires = status.get('rusa_membership_expires') or f'{year}-12-31'
        pills.append({'label': 'RUSA Membership', 'status': f'Active · exp {expires}', 'ok': True})
    elif rider.get('rusa_id'):
        pills.append({'label': 'RUSA Membership', 'status': 'Expired', 'ok': False})
    else:
        pills.append({'label': 'RUSA Membership', 'status': 'Not on file', 'ok': False})
    if status['sfr_member_current']:
        pills.append({'label': 'SFR Membership', 'status': f'Active · {year}', 'ok': True})
    else:
        pills.append({'label': 'SFR Membership', 'status': f'Expired · {year}', 'ok': False})
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
    rider, _ = refresh_rusa_membership(rider, force=True)
    membership = membership_status(rider, refresh_rusa=False)
    status = profile_field_status(rider, rusa_lookup=membership['rusa'])

    if not status['complete']:
        blockers.append('Complete your profile before registering.')
    if rider.get('rusa_id') and membership['rusa']['expired']:
        blockers.append(RUSA_MEMBERSHIP_EXPIRED_BLOCKER)
    if membership['sfr']['expired']:
        blockers.append(SFR_MEMBERSHIP_EXPIRED_BLOCKER)
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
        'membership': membership,
        'membership_expired': status['membership_expired'],
        'rusa_membership_expired': status['rusa_membership_expired'],
        'sfr_membership_expired': status['sfr_membership_expired'],
        'membership_season_year': membership['season_year'],
        'rusa_renew_url': RUSA_RENEW_URL,
        'sfr_renew_url': SFR_RENEW_URL,
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
            'membership_expired': evaluation.get('membership_expired', False),
            'rusa_membership_expired': evaluation.get('rusa_membership_expired', False),
            'sfr_membership_expired': evaluation.get('sfr_membership_expired', False),
            'rusa_renew_url': evaluation.get('rusa_renew_url', RUSA_RENEW_URL),
            'sfr_renew_url': evaluation.get('sfr_renew_url', SFR_RENEW_URL),
            'membership_season_year': evaluation.get('membership_season_year'),
            'membership': evaluation.get('membership'),
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
