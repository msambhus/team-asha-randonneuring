"""Rider signup routes for upcoming rides."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from models import get_ride_by_id, get_signups_for_ride, get_all_riders, signup_rider, remove_signup
from auth import login_required

signup_bp = Blueprint('signup', __name__)


@signup_bp.route('/<int:ride_id>', methods=['GET', 'POST'])
@login_required
def ride_signup(ride_id):
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    if request.method == 'POST':
        rider_id = request.form.get('rider_id', type=int)
        action = request.form.get('action', 'signup')
        if rider_id:
            if action == 'remove':
                remove_signup(rider_id, ride_id)
            else:
                signup_rider(rider_id, ride_id)
        return redirect(url_for('signup.ride_signup', ride_id=ride_id))

    signups = get_signups_for_ride(ride_id)
    signup_ids = {r['id'] for r in signups}
    all_riders = get_all_riders()

    return render_template('signup.html',
                           ride=ride,
                           signups=signups,
                           signup_ids=signup_ids,
                           all_riders=all_riders)
