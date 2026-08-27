"""Worker ride eligibility and ride-mode validation."""
from __future__ import annotations

from datetime import date, timedelta

from brevethub import models

RIDE_MODE_EVENT_DAY = 'event_day'
RIDE_MODE_WORKER_RIDE = 'worker_ride'
RIDE_MODES = frozenset({RIDE_MODE_EVENT_DAY, RIDE_MODE_WORKER_RIDE})


def worker_ride_open(event) -> bool:
    """True when worker ride is enabled and the event week has not fully passed."""
    if not event or not event.get('worker_ride_enabled'):
        return False
    event_date = _as_date(event.get('date'))
    if not event_date:
        return False
    week_end = event_week_bounds(event_date)[1]
    return week_end >= date.today()


def event_week_bounds(event_date: date) -> tuple[date, date]:
    """Sun–Sat week containing the event date."""
    days_since_sunday = (event_date.weekday() + 1) % 7
    week_start = event_date - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _as_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def rider_is_volunteer(rider_id: int, event_id: int) -> bool:
    return bool(models.get_rider_active_volunteer_signups(rider_id, event_id))


def suggested_ride_mode(rider_id: int, event_id: int, *, slot_id: int | None = None) -> str:
    """Default ride mode from volunteer roles (event day if any role allows it)."""
    signups = models.get_rider_active_volunteer_signups(rider_id, event_id)
    if slot_id is not None:
        slot = models.get_volunteer_slot(slot_id)
        if slot and slot.get('allows_ride_on_event_day'):
            return RIDE_MODE_EVENT_DAY
    for signup in signups:
        slot = models.get_volunteer_slot(signup['slot_id'])
        if slot and slot.get('allows_ride_on_event_day'):
            return RIDE_MODE_EVENT_DAY
    return RIDE_MODE_WORKER_RIDE


def ride_mode_stale(registration: dict | None, suggested: str) -> bool:
    """True when an acknowledged plan no longer matches role-based suggestion."""
    if not registration or not registration.get('ride_mode_ack_at'):
        return False
    current = registration.get('ride_mode')
    return current in RIDE_MODES and current != suggested


def reconcile_ride_mode_after_volunteer_change(rider_id: int, event_id: int) -> dict | None:
    """Refresh ride plan when volunteer roles change.

    Drops stale worker-ride choices when roles now suggest event day (or vice versa),
    clears worker ride when the rider is no longer a volunteer, and requires a fresh
    acknowledgment before the updated plan is treated as confirmed.
    """
    event = models.get_brevet_event_registration(event_id)
    if not event or not event.get('worker_ride_enabled'):
        return None

    registration = models.get_event_signup_registration(rider_id, event_id)
    if not registration or not registration.get('ride_mode'):
        return None

    volunteer = rider_is_volunteer(rider_id, event_id)
    current = registration.get('ride_mode')
    acked = bool(registration.get('ride_mode_ack_at'))

    if not volunteer:
        if current == RIDE_MODE_WORKER_RIDE:
            row = models.set_event_signup_ride_mode(
                rider_id, event_id,
                ride_mode=RIDE_MODE_EVENT_DAY,
                acknowledged=False,
            )
            return {
                'reset': True,
                'ride_mode': row.get('ride_mode'),
                'reason': 'no_longer_volunteer',
            }
        return None

    suggested = suggested_ride_mode(rider_id, event_id)
    if acked and current != suggested:
        row = models.set_event_signup_ride_mode(
            rider_id, event_id,
            ride_mode=suggested,
            acknowledged=False,
        )
        return {
            'reset': True,
            'ride_mode': row.get('ride_mode'),
            'reason': 'volunteer_role_changed',
            'suggested_ride_mode': suggested,
        }
    return None


def ride_mode_context(rider_id: int, event_id: int, event) -> dict:
    """Payload for volunteer/registration UIs."""
    registration = models.get_event_signup_registration(rider_id, event_id)
    volunteer = rider_is_volunteer(rider_id, event_id)
    enabled = bool(event.get('worker_ride_enabled'))
    suggested = (
        suggested_ride_mode(rider_id, event_id) if volunteer else RIDE_MODE_EVENT_DAY
    )
    current = (registration or {}).get('ride_mode')
    if current not in RIDE_MODES:
        current = RIDE_MODE_EVENT_DAY if registration else None
    week_start, week_end = event_week_bounds(_as_date(event.get('date')))
    stale = ride_mode_stale(registration, suggested)
    return {
        'worker_ride_enabled': enabled,
        'worker_ride_open': worker_ride_open(event),
        'is_volunteer': volunteer,
        'ride_mode': current,
        'ride_mode_ack_at': (
            str(registration['ride_mode_ack_at'])
            if registration and registration.get('ride_mode_ack_at') else None
        ),
        'has_registration': bool(
            registration and registration.get('registration_status')),
        'needs_ride_mode_choice': bool(
            enabled and volunteer and (
                not registration
                or not registration.get('ride_mode')
                or not registration.get('ride_mode_ack_at')
                or stale
            )),
        'suggested_ride_mode': suggested,
        'ride_mode_stale': stale,
        'event_week_start': week_start.isoformat(),
        'event_week_end': week_end.isoformat(),
    }


def validate_ride_mode(rider_id: int, event: dict, ride_mode: str | None) -> dict:
    """Return {ok, error} for a proposed ride mode at registration."""
    mode = (ride_mode or RIDE_MODE_EVENT_DAY).strip().lower()
    if mode not in RIDE_MODES:
        return {'ok': False, 'error': 'Choose event day or worker ride.'}

    enabled = bool(event.get('worker_ride_enabled'))
    volunteer = rider_is_volunteer(rider_id, event['id'])

    if mode == RIDE_MODE_WORKER_RIDE:
        if not enabled:
            return {'ok': False, 'error': 'Worker ride is not offered for this event.'}
        if not volunteer:
            return {
                'ok': False,
                'error': 'Worker ride is only available to volunteers. Sign up to volunteer first.',
            }
        if not worker_ride_open(event):
            return {'ok': False, 'error': 'Worker ride signup is closed for this event.'}
    return {'ok': True, 'ride_mode': mode}


def set_ride_mode(rider_id: int, event_id: int, ride_mode: str, *, acknowledged: bool = False) -> dict:
    """Persist ride mode on signup row (creates interested row if needed)."""
    event = models.get_brevet_event_registration(event_id)
    if not event:
        return {'ok': False, 'error': 'Event not found.'}
    check = validate_ride_mode(rider_id, event, ride_mode)
    if not check.get('ok'):
        return check
    row = models.set_event_signup_ride_mode(
        rider_id, event_id,
        ride_mode=check['ride_mode'],
        acknowledged=acknowledged,
    )
    if not row:
        return {'ok': False, 'error': 'Could not save ride mode.'}
    return {
        'ok': True,
        'ride_mode': row.get('ride_mode'),
        'ride_mode_ack_at': (
            str(row['ride_mode_ack_at']) if row.get('ride_mode_ack_at') else None
        ),
    }


def ride_mode_label(ride_mode: str | None) -> str:
    if ride_mode == RIDE_MODE_WORKER_RIDE:
        return 'Worker ride'
    if ride_mode == RIDE_MODE_EVENT_DAY:
        return 'Event day'
    return '—'
