"""BrevetHub signup — profile completion after Google sign-in.

Collects an optional RUSA ID (verified against RUSA.org when provided) and a
home club (picker from `rp_club`). Duplicate RUSA claims are rejected so one
official history cannot populate two public identities.
"""
from flask import (
    Blueprint, current_app, redirect, render_template, request,
    session, url_for,
)

from brevethub import models
from brevethub.decorators import current_rider, login_required
from brevethub.redirects import safe_redirect
from brevethub.services.registration import (
    resolve_rusa_id_for_save,
    validate_profile_phones,
)

signup_bp = Blueprint('signup', __name__)


def _signup_response(clubs, *, field_errors=None, form=None, rider=None):
    form = form or {}
    return render_template(
        'signup.html',
        clubs=clubs,
        field_errors=field_errors or {},
        form=form,
        rusa_id=form.get('rusa_id') or '',
        selected_club_id=form.get('club_id', type=int),
        rider=rider,
    )


@signup_bp.route('/', methods=['GET', 'POST'])
@login_required
def signup():
    rider = current_rider()
    if rider is None:
        return redirect(url_for('auth.login'))

    clubs = models.get_all_clubs()

    if request.method == 'POST':
        raw_rusa = (request.form.get('rusa_id') or '').strip()
        club_id = request.form.get('club_id', type=int)
        field_errors = {}
        profile_fields = {
            'first_name': (request.form.get('first_name') or '').strip() or None,
            'last_name': (request.form.get('last_name') or '').strip() or None,
            'city': (request.form.get('city') or '').strip() or None,
            'emergency_name': (request.form.get('emergency_name') or '').strip() or None,
            'sfr_member_year': request.form.get('sfr_member_year', type=int),
        }
        ok, phone_errors, phones = validate_profile_phones(
            request.form.get('phone'),
            request.form.get('emergency_phone'),
        )
        field_errors.update(phone_errors)

        if not club_id or not models.club_exists(club_id):
            field_errors['club_id'] = 'Please pick your home club from the list.'

        rusa_id, rusa_errors = resolve_rusa_id_for_save(
            raw_rusa,
            profile_fields['first_name'],
            profile_fields['last_name'],
        )
        if (
            not rusa_errors
            and rusa_id
            and models.rusa_id_already_claimed(rusa_id, exclude_rider_id=rider['id'])
        ):
            rusa_errors['rusa_id'] = (
                'That RUSA ID is already registered to another BrevetHub account.')
        field_errors.update(rusa_errors)

        if field_errors:
            return _signup_response(
                clubs, field_errors=field_errors, form=request.form, rider=rider)

        profile_fields['phone'] = phones['phone']
        profile_fields['emergency_phone'] = phones['emergency_phone']

        models.complete_rider_profile(
            rider['id'], rusa_id, club_id, rusa_id_duplicate=False)
        models.update_rider_registration_profile(
            rider['id'], club_id=club_id, rusa_id=rusa_id, **profile_fields)
        current_app.logger.info(
            "BrevetHub signup completed: rider=%s club=%s rusa=%s",
            rider['id'], club_id, rusa_id)

        next_url = session.pop('next_url', None)
        return safe_redirect(next_url, 'main.dashboard')

    return _signup_response(clubs, rider=rider)
