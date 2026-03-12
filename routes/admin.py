"""Admin routes: login, dashboard, ride entry, status marking, RWGPS plan generation."""
import json
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from models import (get_current_season, get_rides_for_season, get_riders_for_season,
                    get_ride_by_id, get_participation_matrix, get_clubs,
                    create_ride, update_rider_ride_status, get_all_riders,
                    get_ride_plan_by_rwgps_route_id, create_ride_plan_from_rwgps,
                    auto_finalize_past_rides, get_rides_with_signup_counts,
                    get_strava_admin_summary)
from auth import login_required, user_login_required, verify_password
from services.rwgps import (extract_rwgps_route_id, fetch_route, extract_controls,
                            build_ride_plan, slugify)

admin_bp = Blueprint('admin', __name__)


def _require_admin():
    """Check if current user is an admin. Aborts with 403 if not. Skipped on localhost (debug mode)."""
    from flask import current_app
    if current_app.debug:
        return
    from routes.riders import is_admin_user
    if not is_admin_user():
        abort(403)


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
@user_login_required
def dashboard():
    _require_admin()
    current = get_current_season()
    rides = get_rides_with_signup_counts(current['id']) if current else []
    today = date.today()
    return render_template('admin/dashboard.html', season=current, rides=rides,
                           today=today)


@admin_bp.route('/strava')
@user_login_required
def strava_status():
    _require_admin()
    riders = get_strava_admin_summary()
    return render_template('admin/strava_status.html', riders=riders)


@admin_bp.route('/finalize-past-rides', methods=['POST'])
@user_login_required
def finalize_past_rides():
    _require_admin()
    results = auto_finalize_past_rides()
    if results:
        total = sum(r['riders_finalized'] for r in results)
        ride_names = ', '.join(r['ride_name'] for r in results)
        flash(f'Finalized {total} riders across {len(results)} rides: {ride_names}', 'success')
    else:
        flash('No past rides with pending signups to finalize.', 'info')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/rides/new', methods=['GET', 'POST'])
@user_login_required
def add_ride():
    _require_admin()
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
@user_login_required
def mark_status(ride_id):
    _require_admin()
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
        flash(f'Statuses updated for {ride["name"]}.', 'success')
        return redirect(url_for('admin.dashboard'))

    # Current statuses for this ride
    ride_statuses = {}
    for rider_id, rides_map in matrix.items():
        if ride_id in rides_map:
            ride_statuses[rider_id] = rides_map[ride_id]['status']

    # Sort: riders with a status for this ride come first
    signed_up = [r for r in riders if r['id'] in ride_statuses]
    others = [r for r in riders if r['id'] not in ride_statuses]

    return render_template('admin/mark_status.html',
                           ride=ride,
                           signed_up_riders=signed_up,
                           other_riders=others,
                           ride_statuses=ride_statuses)


# ── RWGPS Plan Generation ────────────────────────────────────────────

@admin_bp.route('/generate-plan', methods=['GET'])
@user_login_required
def generate_plan_form():
    _require_admin()
    return render_template('admin/generate_plan.html')


@admin_bp.route('/generate-plan/preview', methods=['POST'])
@user_login_required
def generate_plan_preview():
    _require_admin()
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
@user_login_required
def generate_plan_save():
    _require_admin()
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


@admin_bp.route('/eddington-check/<int:rider_id>')
@user_login_required
def eddington_check(rider_id):
    """Diagnose Eddington number discrepancies: 3 filter levels, per-year breakdown."""
    _require_admin()
    from flask import current_app, jsonify
    from models import (get_all_strava_activities_for_eddington,
                        get_all_strava_activities_unfiltered, _execute)
    from services.eddington import (calculate_eddington_number, calculate_eddington_by_year,
                                    CYCLING_TYPES)

    conn_row = _execute("""
        SELECT eddington_number_miles, eddington_number_km,
               eddington_calculated_at, backfill_cursor, last_sync_at
        FROM strava_connection WHERE rider_id = %s
    """, (rider_id,)).fetchone()

    type_counts = _execute("""
        SELECT activity_type, COUNT(*) as cnt,
               MIN(start_date_local)::date as oldest,
               MAX(start_date_local)::date as newest
        FROM strava_activity
        WHERE rider_id = %s
        GROUP BY activity_type
        ORDER BY cnt DESC
    """, (rider_id,)).fetchall()

    all_cycling = get_all_strava_activities_for_eddington(rider_id)
    all_unfiltered = get_all_strava_activities_unfiltered(rider_id)
    ride_only = [a for a in all_cycling if a.get('activity_type') == 'Ride']

    recalculated = {}
    for label, acts, at_filter in [
        ('ride_only', ride_only, {'Ride'}),
        ('all_cycling_types', all_cycling, None),
        ('all_types', all_unfiltered, 'all'),
    ]:
        recalculated[label] = {
            'miles': calculate_eddington_number(acts, unit='miles', activity_types=at_filter),
            'km': calculate_eddington_number(acts, unit='km', activity_types=at_filter),
            'activity_count': len(acts),
        }

    by_year_cycling = calculate_eddington_by_year(all_cycling, unit='miles')
    by_year_all = calculate_eddington_by_year(all_unfiltered, unit='miles', activity_types='all')

    per_year = []
    all_years = sorted(set(list(by_year_cycling.keys()) + list(by_year_all.keys())), reverse=True)
    for year in all_years:
        c = by_year_cycling.get(year, {})
        a = by_year_all.get(year, {})
        per_year.append({
            'year': year,
            'eddington_miles': c.get('eddington', 0),
            'cumulative_miles': c.get('eddington_cumulative', 0),
            'ride_days': c.get('ride_days', 0),
            'rides': c.get('rides', 0),
            'all_types_eddington_miles': a.get('eddington', 0),
            'all_types_cumulative_miles': a.get('eddington_cumulative', 0),
            'all_types_days': a.get('ride_days', 0),
            'all_types_count': a.get('rides', 0),
        })

    return jsonify({
        'rider_id': rider_id,
        'stored': {
            'eddington_miles': conn_row['eddington_number_miles'] if conn_row else None,
            'eddington_km': conn_row['eddington_number_km'] if conn_row else None,
            'calculated_at': str(conn_row['eddington_calculated_at']) if conn_row and conn_row['eddington_calculated_at'] else None,
            'backfill_cursor': str(conn_row['backfill_cursor']) if conn_row and conn_row['backfill_cursor'] else None,
            'last_sync_at': str(conn_row['last_sync_at']) if conn_row and conn_row['last_sync_at'] else None,
        },
        'recalculated': recalculated,
        'per_year': per_year,
        'activity_type_distribution': [
            {
                'type': row['activity_type'],
                'count': row['cnt'],
                'oldest': str(row['oldest']) if row['oldest'] else None,
                'newest': str(row['newest']) if row['newest'] else None,
                'included_in_eddington': row['activity_type'] in CYCLING_TYPES,
            }
            for row in type_counts
        ],
    })


@admin_bp.route('/force-resync/<int:rider_id>')
@user_login_required
def force_resync(rider_id):
    """Force re-sync Strava activities for a specific year.

    Usage: /admin/force-resync/6?year=2023
    """
    _require_admin()
    import time as _time
    from flask import current_app, jsonify
    from services.strava import sync_rider_activities
    from models import get_strava_connection

    year = request.args.get('year', type=int)
    if not year:
        return jsonify({'error': 'year parameter required, e.g. ?year=2023'}), 400

    conn = get_strava_connection(rider_id)
    if not conn:
        return jsonify({'error': f'No Strava connection for rider {rider_id}'}), 404

    from datetime import date as _date
    start_of_year = _date(year, 1, 1)
    end_of_year = _date(year, 12, 31)

    after_epoch = int(_time.mktime(start_of_year.timetuple()))
    before_epoch = int(_time.mktime(_date(year + 1, 1, 1).timetuple()))

    current_app.logger.info(
        f'Force re-sync: rider={rider_id} year={year} '
        f'range={start_of_year} to {end_of_year}'
    )

    counts = sync_rider_activities(
        rider_id=rider_id,
        after_epoch=after_epoch,
        before_epoch=before_epoch,
        calculate_eddington=True,
    )

    return jsonify({
        'rider_id': rider_id,
        'year': year,
        'result': counts,
    })
