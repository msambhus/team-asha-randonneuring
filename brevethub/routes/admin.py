"""BrevetHub club-admin surface — owner-gated real ride-plan generation.

A club OWNER (rp_club.owner_rider_id == the signed-in rider) can paste an RWGPS URL
for a calendar brevet and generate a real, RWGPS-backed ride plan, persisted to
rp_brevet_route_plan[_stop] via the reused shared engine (build_ride_plan). The
guest /plan/<event_id> page then renders that persisted plan.

Ownership is the hard gate on every action here:
  - a signed-out visitor is bounced to login,
  - a signed-in rider who owns NO club gets 403,
  - only the rider who owns a club may generate plans (scoped to their club).

Generation FAILS SOFT: a missing RWGPS credential, an unparseable URL, or an RWGPS
API error flashes a message and redirects back — it never 500s (the reused
fetch_route raises on missing keys, so every call is guarded).

Isolation: imports only flask / stdlib / brevethub.* (its own models, decorators,
config) and brevethub.shared.* — nothing from Team Asha — so
test_brevethub_isolation.py stays green. Every model call is on an rp_* table.
"""
from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from brevethub import models
from brevethub.decorators import current_rider, login_required
from brevethub.shared.rwgps import (build_ride_plan, extract_controls,
                                    extract_rwgps_route_id, fetch_route)

admin_bp = Blueprint('admin', __name__)


def _owned_club_or_403():
    """The club the signed-in rider owns, or abort 403 when they own none.

    The single ownership gate for this blueprint. A signed-out rider never reaches
    here (login_required runs first); a signed-in rider who owns no club is 403.
    """
    rider = current_rider()
    owned = models.get_club_owned_by_rider(rider['id']) if rider else None
    if not owned:
        abort(403)
    return rider, owned


@admin_bp.route('/plan', methods=['GET'])
@login_required
def plan_console():
    """Owner console: pick an upcoming brevet + paste an RWGPS URL to generate a plan.

    Owner → 200 with the generate form and a reference list of upcoming events;
    signed-in non-owner → 403.
    """
    _rider, owned = _owned_club_or_403()
    events = models.get_upcoming_events(limit=100)
    return render_template('admin_plan.html', owned_club=owned, events=events)


@admin_bp.route('/plan/generate', methods=['POST'])
@login_required
def generate_plan():
    """Generate + persist a real RWGPS plan for a brevet (owner only).

    Reads event_id + an RWGPS URL (falling back to the event's cached rwgps_url),
    builds the plan via the reused shared engine using the BrevetHub RWGPS
    credentials, and upserts it scoped to the owner's club. Fails soft on any
    RWGPS/build error (flash + redirect, never 500).
    """
    _rider, owned = _owned_club_or_403()

    event_id_raw = (request.form.get('event_id') or '').strip()
    try:
        event_id = int(event_id_raw)
    except (TypeError, ValueError):
        flash('Pick a valid brevet to generate a plan for.', 'error')
        return redirect(url_for('admin.plan_console'))

    event = models.get_brevet_event_full(event_id)
    if not event:
        flash('That brevet is not in the calendar.', 'error')
        return redirect(url_for('admin.plan_console'))

    rwgps_url = (request.form.get('rwgps_url') or '').strip() or event.get('rwgps_url')
    route_id = extract_rwgps_route_id(rwgps_url)
    if not route_id:
        flash('Enter a valid RideWithGPS route URL (e.g. ridewithgps.com/routes/123).',
              'error')
        return redirect(url_for('admin.plan_console'))

    api_key = current_app.config.get('RWGPS_API_KEY')
    auth_token = current_app.config.get('RWGPS_AUTH_TOKEN')
    try:
        route_data = fetch_route(route_id, api_key, auth_token)
        controls = extract_controls(route_data)
        built = build_ride_plan(route_data, controls)
        plan_id = models.upsert_brevet_route_plan(
            event_id, built['plan'], built['stops'], club_id=owned['id'])
    except Exception as e:
        current_app.logger.warning(
            'Admin plan generation failed for event %s (route %s): %s',
            event_id, route_id, e)
        flash(f'Could not generate the plan: {e}', 'error')
        return redirect(url_for('admin.plan_console'))

    if plan_id is None:
        # Another club already owns this brevet's public plan (first-owner-wins).
        flash("This brevet's ride plan is already managed by another club.", 'error')
        return redirect(url_for('admin.plan_console'))

    flash(f'Real ride plan generated for {event["name"]}.', 'success')
    return redirect(url_for('plan.plan_view', event_id=event_id))
