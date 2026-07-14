"""BrevetHub signup — profile completion after Google sign-in.

Collects an OPTIONAL RUSA ID and a club affiliation (picker from `rp_club`) and
writes both to the signed-in rider's `rp_rider` row. v1 does NO RUSA ownership
verification: the RUSA ID is validated for *shape only* (numeric) and a duplicate
claim is *soft-flagged* (`rusa_id_duplicate`), never rejected. Hard verification
(name match against rusa.org) is a deferred follow-on.
"""
from flask import (
    Blueprint, current_app, flash, redirect, render_template, request,
    session, url_for,
)

from brevethub import models
from brevethub.decorators import current_rider, login_required

signup_bp = Blueprint('signup', __name__)


def _normalize_rusa_id(raw):
    """Shape-only RUSA ID check: digits, 1–7 long. Returns the canonical string
    or None if the shape is invalid. No network call, no ownership verification."""
    digits = (raw or '').strip()
    if digits.isdigit() and 1 <= len(digits) <= 7:
        return str(int(digits))  # strip leading zeros to a canonical form
    return None


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

        # Club is required; RUSA ID is optional.
        if not club_id or not models.club_exists(club_id):
            flash('Please pick your club from the list.', 'error')
            return render_template('signup.html', clubs=clubs,
                                   rusa_id=raw_rusa, selected_club_id=club_id)

        rusa_id = None
        rusa_duplicate = False
        if raw_rusa:
            rusa_id = _normalize_rusa_id(raw_rusa)
            if rusa_id is None:
                flash('A RUSA ID is numeric (up to 7 digits). Leave it blank '
                      'if you do not have one.', 'error')
                return render_template('signup.html', clubs=clubs,
                                       rusa_id=raw_rusa,
                                       selected_club_id=club_id)
            # Soft-flag a duplicate claim — v1 does not hard-verify ownership.
            rusa_duplicate = models.rusa_id_already_claimed(
                rusa_id, exclude_rider_id=rider['id'])
            if rusa_duplicate:
                current_app.logger.info(
                    "BrevetHub duplicate RUSA-ID claim flagged: rider=%s rusa=%s",
                    rider['id'], rusa_id)

        models.complete_rider_profile(
            rider['id'], rusa_id, club_id, rusa_id_duplicate=rusa_duplicate)
        current_app.logger.info(
            "BrevetHub signup completed: rider=%s club=%s rusa=%s",
            rider['id'], club_id, rusa_id)

        next_url = session.pop('next_url', None)
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect(url_for('main.dashboard'))

    return render_template('signup.html', clubs=clubs,
                           rusa_id=rider.get('rusa_id') or '',
                           selected_club_id=rider.get('club_id'))
