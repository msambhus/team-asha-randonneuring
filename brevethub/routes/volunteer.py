"""Volunteer signup — slot picker for logged-in riders."""
from flask import Blueprint, jsonify, request, url_for

from brevethub import models
from brevethub.decorators import current_rider, login_required
from brevethub.services.registration import profile_field_status, rider_display_name
from brevethub.services.volunteer import (
    signup_for_slot,
    slot_payload,
    volunteer_open,
    withdraw_signup,
)
from brevethub.services.worker_ride import ride_mode_context, set_ride_mode

volunteer_bp = Blueprint('volunteer', __name__)


def _login_required_json():
    return jsonify({
        'error': 'Sign in to volunteer for a brevet.',
        'login_url': url_for('auth.login', next=url_for('calendar.calendar')),
    }), 401


def _event_or_404(event_id):
    event = models.get_brevet_event_registration(event_id)
    if not event:
        return None, (jsonify({'error': 'Event not found'}), 404)
    return event, None


@volunteer_bp.route('/calendar/<int:event_id>/volunteer/slots')
def volunteer_slots(event_id):
    """Public slot list with availability counts."""
    event, err = _event_or_404(event_id)
    if err:
        return err
    slots = models.get_volunteer_slots_for_event(event_id)
    return jsonify({
        'event': {
            'id': event['id'],
            'name': event['name'],
            'date': str(event['date']),
            'volunteer_enabled': bool(event.get('volunteer_enabled')),
            'worker_ride_enabled': bool(event.get('worker_ride_enabled')),
            'volunteer_open': volunteer_open(event, slot_count=len(slots)),
        },
        'slots': [slot_payload(s) for s in slots],
    })


@volunteer_bp.route('/calendar/<int:event_id>/volunteer/status')
@login_required
def volunteer_status(event_id):
    """Signed-in rider's volunteer signups for this event."""
    event, err = _event_or_404(event_id)
    if err:
        return err
    rider = current_rider()
    signups = models.get_rider_volunteer_signups_for_event(rider['id'], event_id)
    return jsonify({
        'signups': signups,
        'ride_mode': ride_mode_context(rider['id'], event_id, event),
        'profile': {
            'name': rider_display_name(rider),
            'complete': profile_field_status(rider)['complete'],
        },
    })


@volunteer_bp.route('/calendar/<int:event_id>/volunteer/signup', methods=['POST'])
@login_required
def volunteer_signup(event_id):
    """Pick one volunteer slot."""
    rider = current_rider()
    if not rider:
        return _login_required_json()

    event, err = _event_or_404(event_id)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    slot_id = payload.get('slot_id')
    try:
        slot_id = int(slot_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'Select a volunteer role.'}), 400

    slot = models.get_volunteer_slot(slot_id)
    if not slot or slot['event_id'] != event_id:
        return jsonify({'error': 'Invalid volunteer role for this event.'}), 400

    result = signup_for_slot(rider, slot_id)
    if not result.get('ok'):
        return jsonify(result), 409 if 'full' in result.get('error', '').lower() else 400
    return jsonify(result)


@volunteer_bp.route('/volunteer/signup/<int:signup_id>/withdraw', methods=['POST'])
@login_required
def volunteer_withdraw(signup_id):
    rider = current_rider()
    result = withdraw_signup(rider, signup_id)
    if not result.get('ok'):
        return jsonify(result), 404
    return jsonify(result)


@volunteer_bp.route('/calendar/<int:event_id>/ride-mode', methods=['GET'])
@login_required
def ride_mode_status(event_id):
    """Ride mode context for volunteers (event day vs worker ride)."""
    event, err = _event_or_404(event_id)
    if err:
        return err
    rider = current_rider()
    return jsonify({
        'ride_mode': ride_mode_context(rider['id'], event_id, event),
    })


@volunteer_bp.route('/calendar/<int:event_id>/ride-mode', methods=['POST'])
@login_required
def ride_mode_set(event_id):
    """Save acknowledged ride mode choice for a volunteer."""
    rider = current_rider()
    event, err = _event_or_404(event_id)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    ride_mode = payload.get('ride_mode')
    acknowledged = bool(payload.get('acknowledged'))
    if not acknowledged:
        return jsonify({'error': 'Please confirm your ride plan.'}), 400

    result = set_ride_mode(
        rider['id'], event_id, ride_mode, acknowledged=True)
    if not result.get('ok'):
        return jsonify(result), 400
    return jsonify(result)
