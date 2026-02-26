"""Admin routes: login, dashboard, ride entry, status marking, RWGPS plan generation."""
import json
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from models import (get_current_season, get_rides_for_season, get_riders_for_season,
                    get_ride_by_id, get_participation_matrix, get_clubs,
                    create_ride, update_rider_ride_status, get_all_riders,
                    get_ride_plan_by_rwgps_route_id, create_ride_plan_from_rwgps)
from auth import login_required, verify_password
from services.rwgps import (extract_rwgps_route_id, fetch_route, extract_controls,
                            build_ride_plan, slugify)

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if verify_password(password):
            session['logged_in'] = True
            next_url = request.args.get('next', url_for('admin.dashboard'))
            return redirect(next_url)
        else:
            return render_template('admin/login.html', error='Invalid password')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('main.index'))


@admin_bp.route('/')
@login_required
def dashboard():
    current = get_current_season()
    rides = get_rides_for_season(current['id']) if current else []
    return render_template('admin/dashboard.html', season=current, rides=rides)


@admin_bp.route('/rides/new', methods=['GET', 'POST'])
@login_required
def add_ride():
    current = get_current_season()
    clubs = get_clubs()

    if request.method == 'POST':
        name = request.form['name']
        ride_type = request.form.get('ride_type', 'BRM')
        ride_date = request.form['date']
        distance_km = int(request.form['distance_km'])
        club_id = request.form.get('club_id', type=int)
        elevation_ft = request.form.get('elevation_ft', type=int)
        distance_miles = request.form.get('distance_miles', type=float)
        ft_per_mile = request.form.get('ft_per_mile', type=float)
        rwgps_url = request.form.get('rwgps_url', '').strip() or None

        ride_id = create_ride(
            season_id=current['id'],
            club_id=club_id,
            name=name,
            ride_type=ride_type,
            ride_date=ride_date,
            distance_km=distance_km,
            elevation_ft=elevation_ft,
            distance_miles=distance_miles,
            ft_per_mile=ft_per_mile,
            rwgps_url=rwgps_url,
        )
        return redirect(url_for('admin.mark_status', ride_id=ride_id))

    return render_template('admin/add_ride.html', season=current, clubs=clubs)


@admin_bp.route('/rides/<int:ride_id>/status', methods=['GET', 'POST'])
@login_required
def mark_status(ride_id):
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    current = get_current_season()
    riders = get_all_riders()
    matrix = get_participation_matrix(current['id']) if current else {}

    if request.method == 'POST':
        statuses = {}
        for r in riders:
            val = request.form.get(f'status_{r["id"]}', '').strip()
            if val:
                statuses[r['id']] = val
        update_rider_ride_status(ride_id, statuses)
        return redirect(url_for('admin.dashboard'))

    # Current statuses for this ride
    ride_statuses = {}
    for rider_id, rides in matrix.items():
        if ride_id in rides:
            ride_statuses[rider_id] = rides[ride_id]['status']

    return render_template('admin/mark_status.html',
                           ride=ride,
                           riders=riders,
                           ride_statuses=ride_statuses)


# ── RWGPS Plan Generation ────────────────────────────────────────────

@admin_bp.route('/generate-plan', methods=['GET'])
@login_required
def generate_plan_form():
    return render_template('admin/generate_plan.html')


@admin_bp.route('/generate-plan/preview', methods=['POST'])
@login_required
def generate_plan_preview():
    rwgps_url = request.form.get('rwgps_url', '').strip()
    if not rwgps_url:
        flash('Please enter a RideWithGPS URL.', 'error')
        return redirect(url_for('admin.generate_plan_form'))

    route_id = extract_rwgps_route_id(rwgps_url)
    if not route_id:
        flash('Could not extract route ID from that URL. Use a URL like https://ridewithgps.com/routes/12345', 'error')
        return redirect(url_for('admin.generate_plan_form'))

    # Check for existing plan
    existing = get_ride_plan_by_rwgps_route_id(route_id)

    try:
        route_data = fetch_route(route_id)
        controls = extract_controls(route_data)
        result = build_ride_plan(route_data, controls)
    except Exception as e:
        flash(f'Error fetching route: {e}', 'error')
        return redirect(url_for('admin.generate_plan_form'))

    return render_template('admin/generate_plan_preview.html',
                           plan=result['plan'],
                           stops=result['stops'],
                           existing=existing,
                           plan_json=json.dumps(result))


@admin_bp.route('/generate-plan/save', methods=['POST'])
@login_required
def generate_plan_save():
    plan_json_str = request.form.get('plan_json', '')
    name_override = request.form.get('plan_name', '').strip()

    if not plan_json_str:
        flash('No plan data to save.', 'error')
        return redirect(url_for('admin.generate_plan_form'))

    try:
        result = json.loads(plan_json_str)
    except json.JSONDecodeError:
        flash('Invalid plan data.', 'error')
        return redirect(url_for('admin.generate_plan_form'))

    plan_data = result['plan']
    stops_data = result['stops']

    # Apply name override if admin edited it
    if name_override and name_override != plan_data['name']:
        plan_data['name'] = name_override
        plan_data['slug'] = slugify(name_override)

    try:
        plan_id = create_ride_plan_from_rwgps(plan_data, stops_data)
        flash(f'Ride plan "{plan_data["name"]}" saved successfully!', 'success')
    except Exception as e:
        flash(f'Error saving plan: {e}', 'error')
        return redirect(url_for('admin.generate_plan_form'))

    return redirect(url_for('riders.ride_plan_detail', slug=plan_data['slug']))
