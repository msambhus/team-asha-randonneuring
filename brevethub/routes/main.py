"""BrevetHub public shell + rider dashboard.

- `/`          neutral landing page with the "Sign in with Google" entry point.
- `/dashboard` a signed-in rider's home (profile required).

The guest/spectator public-ride browse is deferred to a follow-on mission
(the `rp_ride.is_public` flag and the `get_public_rides` helper are created now
so no later migration or model change alters this baseline).
"""
from flask import Blueprint, redirect, render_template, url_for

from brevethub import models
from brevethub.decorators import current_rider, profile_required

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def landing():
    """De-branded landing. Signed-in riders go straight to their dashboard."""
    if current_rider():
        return redirect(url_for('main.dashboard'))
    return render_template('landing.html')


@main_bp.route('/dashboard')
@profile_required
def dashboard():
    rider = current_rider()
    club = models.get_club(rider['club_id']) if rider.get('club_id') else None
    return render_template('dashboard.html', rider=rider, club=club)
