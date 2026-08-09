"""Brevet registration wizard — profile, waiver, confirmation (no payments).

Integrated with the brevet calendar: event cards open a 3-step modal and POST to
these JSON endpoints. Successful confirmation sets rp_event_signup to going plus
registration_status confirmed/exception/waitlist.
"""
from flask import Blueprint, jsonify, render_template, request, url_for

from brevethub import models
from brevethub.decorators import current_rider, login_required
from brevethub.redirects import is_safe_relative_url, safe_redirect
from brevethub.services.registration import (
    evaluate_registration,
    membership_pills,
    profile_field_status,
    profile_payload,
    confirm_registration_for_event,
    rider_display_name,
    registration_open,
    resolve_rusa_id_for_save,
    validate_profile_phones,
)

_TEAM_EVENT_TYPES = frozenset({'acp flèche', 'acp fleche', 'rusa arrow/dart/dart populaire', 'rusa dart'})

register_bp = Blueprint('register', __name__)


def _profile_cancel_url():
    next_url = request.args.get('next')
    if is_safe_relative_url(next_url):
        return next_url
    return url_for('main.profile')


def _profile_edit_response(rider, clubs, *, field_errors=None):
    return render_template(
        'profile_edit.html',
        rider=rider,
        clubs=clubs,
        field_errors=field_errors or {},
        field_status=profile_field_status(rider),
        membership_pills=membership_pills(rider),
        cancel_url=_profile_cancel_url(),
    )


def _rider_from_form(rider, form):
    """Re-render the profile form with the rider's submitted values."""
    updated = dict(rider)
    for key in (
        'first_name', 'last_name', 'phone', 'city',
        'emergency_name', 'emergency_phone', 'rusa_id',
    ):
        val = (form.get(key) or '').strip()
        updated[key] = val or None
    sfr_year = form.get('sfr_member_year', type=int)
    updated['sfr_member_year'] = sfr_year
    club_id = form.get('club_id', type=int)
    if club_id:
        updated['club_id'] = club_id
    return updated


def _login_required_json():
    return jsonify({
        'error': 'Sign in to register for a brevet.',
        'login_url': url_for('auth.login', next=url_for('calendar.calendar')),
    }), 401


def _event_or_404(event_id):
    event = models.get_brevet_event_registration(event_id)
    if not event:
        return None, (jsonify({'error': 'Event not found'}), 404)
    return event, None


def _controls_for_event(event_id):
    bundle = models.get_brevet_route_plan_with_stops(event_id)
    if not bundle:
        return []
    controls = []
    for stop in bundle['stops']:
        if (stop.get('stop_type') or '').lower() in ('start', 'finish'):
            continue
        name = stop.get('location') or f"Control {stop.get('stop_order')}"
        miles = stop.get('distance_miles')
        if miles is not None:
            km = round(float(miles) * 1.60934)
            controls.append(f'{name} · {km}km')
    return controls[:8]


@register_bp.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    rider = current_rider()
    clubs = models.get_all_clubs()
    if request.method == 'POST':
        raw_rusa = (request.form.get('rusa_id') or '').strip()
        club_id = request.form.get('club_id', type=int)
        sfr_year = request.form.get('sfr_member_year', type=int)
        form_rider = _rider_from_form(rider, request.form)
        field_errors = {}
        first_name = (request.form.get('first_name') or '').strip() or None
        last_name = (request.form.get('last_name') or '').strip() or None
        rusa_id, rusa_errors = resolve_rusa_id_for_save(
            raw_rusa, first_name, last_name)
        if (
            not rusa_errors
            and rusa_id
            and models.rusa_id_already_claimed(rusa_id, exclude_rider_id=rider['id'])
        ):
            rusa_errors['rusa_id'] = (
                'That RUSA ID is already registered to another account.')
        field_errors.update(rusa_errors)
        if not club_id or not models.club_exists(club_id):
            field_errors['club_id'] = 'Please choose your home club.'

        ok, phone_errors, phones = validate_profile_phones(
            request.form.get('phone'),
            request.form.get('emergency_phone'),
        )
        field_errors.update(phone_errors)
        if field_errors:
            return _profile_edit_response(form_rider, clubs, field_errors=field_errors)

        rider = models.update_rider_registration_profile(
            rider['id'],
            first_name=(request.form.get('first_name') or '').strip() or None,
            last_name=(request.form.get('last_name') or '').strip() or None,
            phone=phones['phone'],
            city=(request.form.get('city') or '').strip() or None,
            emergency_name=(request.form.get('emergency_name') or '').strip() or None,
            emergency_phone=phones['emergency_phone'],
            sfr_member_year=sfr_year,
            rusa_id=rusa_id,
            club_id=club_id,
        )
        models.complete_rider_profile(
            rider['id'], rider.get('rusa_id'), club_id,
            rusa_id_duplicate=False)
        from flask import flash
        flash('Profile saved.', 'success')
        return safe_redirect(request.args.get('next'), 'main.profile')

    return _profile_edit_response(rider, clubs)


def _profile_response(rider):
    rider = models.get_rider_by_id(rider['id'])
    return {
        'profile': profile_payload(
            rider, edit_url=url_for('register.edit_profile')),
        'field_status': profile_field_status(rider),
        'membership_pills': membership_pills(rider),
    }


@register_bp.route('/register/profile/quick-save', methods=['POST'])
@login_required
def quick_save_profile():
    """Save profile edits inline during registration without leaving the wizard."""
    rider = current_rider()
    payload = request.get_json(silent=True) or {}
    raw_rusa = (payload.get('rusa_id') or '').strip()
    first_name = (payload.get('first_name') or rider.get('first_name') or '').strip() or None
    last_name = (payload.get('last_name') or rider.get('last_name') or '').strip() or None
    rusa_id = rider.get('rusa_id')
    if raw_rusa:
        rusa_id, rusa_errors = resolve_rusa_id_for_save(
            raw_rusa, first_name, last_name)
        if (
            not rusa_errors
            and rusa_id
            and models.rusa_id_already_claimed(rusa_id, exclude_rider_id=rider['id'])
        ):
            return jsonify({'error': 'That RUSA ID is already registered.'}), 409
        if rusa_errors:
            return jsonify({
                'error': rusa_errors['rusa_id'],
                'field_errors': rusa_errors,
            }), 400
    elif raw_rusa == '' and 'rusa_id' in payload:
        rusa_id = None

    sfr_year = payload.get('sfr_member_year')
    if sfr_year in ('', None):
        sfr_year = None
    else:
        try:
            sfr_year = int(sfr_year)
        except (TypeError, ValueError):
            return jsonify({'error': 'SFR membership year must be a number.'}), 400

    ok, field_errors, phones = validate_profile_phones(
        payload.get('phone'),
        payload.get('emergency_phone'),
    )
    if not ok:
        return jsonify({
            'error': next(iter(field_errors.values())),
            'field_errors': field_errors,
        }), 400

    models.update_rider_registration_profile(
        rider['id'],
        first_name=(payload.get('first_name') or '').strip() or None,
        last_name=(payload.get('last_name') or '').strip() or None,
        phone=phones['phone'],
        city=(payload.get('city') or '').strip() or None,
        emergency_name=(payload.get('emergency_name') or '').strip() or None,
        emergency_phone=phones['emergency_phone'],
        sfr_member_year=sfr_year,
        rusa_id=rusa_id,
    )
    if rider.get('club_id'):
        models.complete_rider_profile(
            rider['id'], rusa_id, rider['club_id'], rusa_id_duplicate=False)
    updated = models.get_rider_by_id(rider['id'])
    resp = _profile_response(updated)
    return jsonify({'ok': True, **resp})


@register_bp.route('/register/bulk/preview', methods=['POST'])
@login_required
def bulk_preview():
    """Validate a set of event ids before bulk registration."""
    rider = current_rider()
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get('event_ids') or []
    event_ids = []
    for raw in raw_ids:
        try:
            event_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    events = []
    blockers = []
    for eid in event_ids:
        event = models.get_brevet_event_registration(eid)
        if not event:
            blockers.append(f'Event #{eid} not found.')
            continue
        confirmed = models.get_event_registration_count(eid)
        evaluation = evaluate_registration(rider, event, confirmed_count=confirmed)
        existing = models.get_event_signup_registration(rider['id'], eid)
        if not evaluation['ok']:
            blockers.extend(evaluation['blockers'])
        events.append({
            'id': eid,
            'name': event['name'],
            'date': str(event['date']),
            'distance_km': event['distance_km'],
            'fee_cents': event.get('fee_cents'),
            'evaluation': evaluation,
            'already_registered': bool(
                existing and existing.get('registration_status')),
        })
    waiver = models.get_waiver_for_event(
        models.get_brevet_event_registration(event_ids[0]) if event_ids else None)
    return jsonify({
        'events': events,
        'profile': _profile_response(rider)['profile'],
        'field_status': profile_field_status(rider),
        'evaluation': {'blockers': list(dict.fromkeys(blockers))},
        'waiver': {
            'version_id': waiver['id'] if waiver else None,
            'text': waiver['waiver_text'] if waiver else '',
        },
        'blockers': blockers,
    })


@register_bp.route('/calendar/<int:event_id>/register/details')
def register_details(event_id):
    """Step 1 payload: event facts + capacity."""
    event, err = _event_or_404(event_id)
    if err:
        return err
    confirmed = models.get_event_registration_count(event_id)
    spots_open = None
    if event.get('capacity') is not None:
        spots_open = max(0, int(event['capacity']) - confirmed)
    return jsonify({
        'event': {
            'id': event['id'],
            'name': event['name'],
            'date': str(event['date']),
            'distance_km': event['distance_km'],
            'ride_type': event.get('ride_type'),
            'start_time': event.get('start_time'),
            'start_location': event.get('start_location'),
            'time_limit_hours': float(event['time_limit_hours']) if event.get('time_limit_hours') is not None else None,
            'fee_cents': event.get('fee_cents'),
            'registration_deadline': str(event['registration_deadline']) if event.get('registration_deadline') else None,
            'capacity': event.get('capacity'),
            'confirmed_count': confirmed,
            'spots_open': spots_open,
            'registration_enabled': bool(event.get('registration_enabled')),
            'registration_open': registration_open(event, confirmed_count=confirmed),
            'summary': event.get('event_summary'),
            'controls': _controls_for_event(event_id),
            'is_team_event': (event.get('ride_type') or '').lower() in _TEAM_EVENT_TYPES,
        },
    })


@register_bp.route('/calendar/<int:event_id>/register/profile')
@login_required
def register_profile(event_id):
    """Step 2 payload: rider profile + validation hints."""
    event, err = _event_or_404(event_id)
    if err:
        return err
    rider = current_rider()
    waiver = models.get_waiver_for_event(event)
    confirmed = models.get_event_registration_count(event_id)
    evaluation = evaluate_registration(rider, event, confirmed_count=confirmed)
    existing = models.get_event_signup_registration(rider['id'], event_id)
    return jsonify({
        'profile': profile_payload(
            rider, edit_url=url_for('register.edit_profile', next=request.path)),
        'field_status': profile_field_status(rider),
        'evaluation': evaluation,
        'waiver': {
            'version_id': waiver['id'] if waiver else None,
            'version_label': waiver['version_label'] if waiver else None,
            'text': waiver['waiver_text'] if waiver else '',
        },
        'existing_registration': existing,
        'membership_pills': membership_pills(rider),
    })


@register_bp.route('/calendar/<int:event_id>/register/confirm', methods=['POST'])
@login_required
def register_confirm(event_id):
    """Step 3: accept waiver + confirm registration (no payment)."""
    rider = current_rider()
    if not rider:
        return _login_required_json()

    event, err = _event_or_404(event_id)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    waiver_version_id = payload.get('waiver_version_id')
    waiver_accepted = bool(payload.get('waiver_accepted'))
    if not waiver_accepted or not waiver_version_id:
        return jsonify({'error': 'Waiver acceptance is required.'}), 400

    waiver = models.get_waiver_for_event(event)
    if not waiver or int(waiver_version_id) != int(waiver['id']):
        return jsonify({'error': 'Waiver version mismatch. Refresh and try again.'}), 400

    result = confirm_registration_for_event(
        rider, event, waiver=waiver, waiver_accepted=True)
    if not result.get('ok'):
        code = 409 if 'posted result' in result.get('error', '') else 400
        return jsonify(result), code

    # Record enhanced waiver data
    try:
        profile_snap = models.get_rider_by_id(rider['id'])
        models.record_waiver_acceptance_v2(
            event_id, rider['id'], int(waiver_version_id),
            dict(profile_snap) if profile_snap else {},
            is_minor=bool(payload.get('is_minor')),
            signatory_name=(payload.get('signatory_name') or '').strip() or None,
            guardian_name=(payload.get('guardian_name') or '').strip() or None,
            guardian_phone=(payload.get('guardian_phone') or '').strip() or None,
            age_certified=bool(payload.get('age_certified')),
            esign_consented=bool(payload.get('esign_consented')),
            waiver_method=payload.get('waiver_method') or 'in_app',
            initials=payload.get('waiver_initials') or None,
            waiver_signed_date=payload.get('waiver_signed_date') or None,
        )
    except Exception:
        pass  # Waiver v2 columns added by migration; tolerate if not yet deployed

    return jsonify({
        **result,
        'rider_name': rider_display_name(rider),
        'email': rider.get('email'),
        'event': {
            **result['event'],
            'ride_type': event.get('ride_type'),
            'time_limit_hours': float(event['time_limit_hours']) if event.get('time_limit_hours') is not None else None,
            'start_location': event.get('start_location'),
        },
    })


@register_bp.route('/register/bulk/confirm', methods=['POST'])
@login_required
def bulk_confirm():
    """Confirm registration for multiple events with one waiver acceptance."""
    rider = current_rider()
    payload = request.get_json(silent=True) or {}
    if not payload.get('waiver_accepted'):
        return jsonify({'error': 'Waiver acceptance is required.'}), 400
    waiver_version_id = payload.get('waiver_version_id')
    raw_ids = payload.get('event_ids') or []
    event_ids = []
    for raw in raw_ids:
        try:
            event_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not event_ids:
        return jsonify({'error': 'Select at least one event.'}), 400

    confirmed_results = []
    failed = []
    for eid in event_ids:
        event = models.get_brevet_event_registration(eid)
        if not event:
            failed.append({'event_id': eid, 'error': 'Event not found'})
            continue
        waiver = models.get_waiver_for_event(event)
        if not waiver or int(waiver_version_id) != int(waiver['id']):
            failed.append({'event_id': eid, 'error': 'Waiver version mismatch'})
            continue
        existing = models.get_event_signup_registration(rider['id'], eid)
        if existing and existing.get('registration_status'):
            failed.append({'event_id': eid, 'error': 'Already registered'})
            continue
        result = confirm_registration_for_event(
            rider, event, waiver=waiver, waiver_accepted=True)
        if result.get('ok'):
            confirmed_results.append(result)
        else:
            failed.append({'event_id': eid, 'error': result.get('error')})

    if not confirmed_results:
        return jsonify({'ok': False, 'failed': failed}), 400

    return jsonify({
        'ok': True,
        'confirmed': confirmed_results,
        'failed': failed,
        'rider_name': rider_display_name(rider),
        'email': rider.get('email'),
    })




@register_bp.route('/rusa/validate-batch', methods=['POST'])
@login_required
def validate_rusa_batch():
    """Validate a list of {rusa_id, first_name, last_name} combos against RUSA.org."""
    from brevethub.services.registration import validate_rusa_profile_fields
    payload = request.get_json(silent=True) or {}
    members = payload.get('members', [])
    results = []
    for m in members:
        rusa_id = (m.get('rusa_id') or '').strip()
        first_name = (m.get('first_name') or '').strip()
        last_name = (m.get('last_name') or '').strip()
        if not rusa_id or not first_name or not last_name:
            results.append({'rusa_id': rusa_id, 'ok': False,
                            'error': 'RUSA #, first name, and last name are all required.'})
            continue
        try:
            _id, errors = validate_rusa_profile_fields(rusa_id, first_name, last_name)
            results.append({'rusa_id': rusa_id, 'ok': not errors, 'error': errors[0] if errors else None})
        except Exception as e:
            results.append({'rusa_id': rusa_id, 'ok': False, 'error': str(e)})
    return jsonify({'results': results})


@register_bp.route('/calendar/<int:event_id>/register/team', methods=['POST'])
@login_required
def register_team(event_id):
    """Register the signed-in rider as a team captain for a team event (Flèche/Dart)."""
    rider = current_rider()
    if not rider:
        return _login_required_json()

    event, err = _event_or_404(event_id)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    team_name = (payload.get('team_name') or '').strip()
    if not team_name:
        return jsonify({'error': 'Team name is required.'}), 400

    proof_method = payload.get('proof_method') or 'brevet_card'
    rwgps_url = (payload.get('rwgps_url') or '').strip() or None
    needs_special_review = bool(payload.get('needs_special_review'))
    notes_raw = (payload.get('notes') or '').strip()
    if needs_special_review:
        notes_raw = ('[NEEDS SPECIAL REVIEW: >5 members, tandems required] ' + notes_raw).strip()
    notes = notes_raw or None

    existing = models.get_rider_team_registration(rider['id'], event_id)
    if existing:
        return jsonify({'error': 'You have already registered a team for this event.'}), 409

    ride_type = (event.get('ride_type') or '').lower()
    if 'flèche' in ride_type or 'fleche' in ride_type:
        team_event_type = 'fleche'
    elif 'dart' in ride_type or 'arrow' in ride_type:
        team_event_type = 'dart'
    else:
        team_event_type = 'team'

    import string, random
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

    team_id = models.create_team_registration(
        event_id=event_id,
        captain_rider_id=rider['id'],
        team_name=team_name,
        team_event_type=team_event_type,
        proof_method=proof_method,
        rwgps_url=rwgps_url,
        notes=notes,
    )
    if not team_id:
        return jsonify({'error': 'Failed to create team registration.'}), 500

    members = payload.get('members') or []
    for i, m in enumerate(members):
        first = (m.get('first_name') or '').strip() or None
        last = (m.get('last_name') or '').strip() or None
        rusa = (m.get('rusa_id') or '').strip() or None
        if first or last or rusa:
            models.add_team_member(
                team_registration_id=team_id,
                member_order=i + 2,
                first_name=first,
                last_name=last,
                rusa_id=rusa,
            )

    return jsonify({
        'ok': True,
        'team_id': team_id,
        'team_name': team_name,
        'confirmation_code': code,
        'event': {
            'id': event_id,
            'name': event['name'],
            'date': str(event['date']),
        },
    })
