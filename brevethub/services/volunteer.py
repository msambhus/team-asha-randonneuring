"""Volunteer slot signup validation and confirmation."""
from __future__ import annotations

from datetime import date

from brevethub import models
from brevethub.services.registration import rider_display_name


def volunteer_open(event, *, slot_count=None):
    """True when volunteer signup is enabled and the event has not passed."""
    if not event or not event.get('volunteer_enabled'):
        return False
    event_date = event.get('date')
    if event_date:
        if isinstance(event_date, str):
            event_date = date.fromisoformat(event_date[:10])
        if event_date < date.today():
            return False
    if slot_count is not None:
        return slot_count > 0
    return models.count_volunteer_slots(event['id']) > 0


def slot_payload(slot, *, confirmed_count=None):
    """Public slot dict with availability for the signup UI."""
    if confirmed_count is None:
        confirmed_count = models.count_slot_confirmed_signups(slot['id'])
    capacity = int(slot.get('capacity') or 1)
    available = max(0, capacity - confirmed_count)
    return {
        'id': slot['id'],
        'role_name': slot['role_name'],
        'description': slot.get('description'),
        'capacity': capacity,
        'confirmed_count': confirmed_count,
        'available': available,
        'full': available <= 0,
    }


def signup_for_slot(rider, slot_id):
    """Sign a rider up for one volunteer slot.

    First slot on an event is confirmed when capacity allows. Additional slots
    on the same event are flagged ``exception`` for admin approval.
    """
    slot = models.get_volunteer_slot(slot_id)
    if not slot:
        return {'ok': False, 'error': 'Volunteer role not found.'}

    event = models.get_brevet_event_registration(slot['event_id'])
    if not event:
        return {'ok': False, 'error': 'Event not found.'}
    if not volunteer_open(event, slot_count=1):
        return {'ok': False, 'error': 'Volunteer signup is not open for this event.'}

    existing = models.get_volunteer_signup_for_slot_rider(slot_id, rider['id'])
    if existing and existing.get('status') != 'withdrawn':
        return {'ok': False, 'error': 'You are already signed up for this role.'}

    confirmed_count = models.count_slot_confirmed_signups(slot_id)
    capacity = int(slot.get('capacity') or 1)
    if confirmed_count >= capacity:
        return {'ok': False, 'error': 'This role is full.'}

    active = models.get_rider_active_volunteer_signups(rider['id'], slot['event_id'])
    status = 'exception' if active else 'confirmed'

    row = models.upsert_volunteer_signup(
        slot_id, rider['id'], status=status,
        approved_by=None if status == 'exception' else 'auto',
    )
    if not row:
        return {'ok': False, 'error': 'Could not save volunteer signup.'}

    return {
        'ok': True,
        'status': row['status'],
        'signup_id': row['id'],
        'slot': slot_payload(slot, confirmed_count=confirmed_count + (1 if status == 'confirmed' else 0)),
        'rider_name': rider_display_name(rider),
        'event': {
            'id': event['id'],
            'name': event['name'],
            'date': str(event['date']),
        },
        'needs_approval': status == 'exception',
        'message': (
            'Your additional volunteer role is pending organizer approval.'
            if status == 'exception'
            else 'You are signed up to volunteer.'
        ),
    }


def withdraw_signup(rider, signup_id):
    """Rider withdraws their own volunteer signup."""
    signup = models.get_volunteer_signup(signup_id)
    if not signup:
        return {'ok': False, 'error': 'Signup not found.'}
    if signup['rider_id'] != rider['id']:
        return {'ok': False, 'error': 'Not your signup.'}
    models.set_volunteer_signup_status(signup_id, 'withdrawn')
    return {'ok': True}
