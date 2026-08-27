"""Admin routes: login, dashboard, ride entry, status marking, RWGPS plan generation."""
import json
import os
from datetime import date
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort, jsonify, current_app
from models import (get_current_season, get_rides_for_season, get_riders_for_season,
                    get_ride_by_id, get_participation_matrix, get_clubs,
                    create_ride, update_rider_ride_status, get_all_riders,
                    get_ride_plan_by_rwgps_route_id, create_ride_plan_from_rwgps,
                    auto_finalize_past_rides, get_rides_with_signup_counts,
                    get_strava_admin_summary, get_all_active_strava_connections,
                    get_all_strava_activities_for_eddington, update_eddington_number,
                    get_strava_connection,
                    admin_delete_rider_ride,
                    get_all_personality_profiles, get_personality_profile,
                    upsert_personality_profile, get_trait_evidence,
                    get_rider_by_id,
                    get_gear_preference, upsert_gear_preference,
                    get_coach_assignments, upsert_coach_assignment,
                    get_all_guardrails, insert_guardrail,
                    update_guardrail, soft_delete_guardrail,
                    update_ride_core, update_ride_details)
from auth import login_required, user_login_required, verify_password
from services.rwgps import (extract_rwgps_route_id, fetch_route, extract_controls,
                            build_ride_plan, slugify)
from shared.operations_status import route_plan_status

admin_bp = Blueprint('admin', __name__)

# ── Personality & Coaching constants ─────────────────────────────────
PERSONALITY_ENUMS = {
    'tone': ['direct', 'warm', 'playful', 'serious', 'sarcastic'],
    'humor_type': ['none', 'dry', 'sarcastic', 'gentle', 'self-deprecating'],
    'directness': ['low', 'medium', 'high'],
    'encouragement_style': ['data-driven', 'emotional', 'balanced', 'tough-love'],
    'technical_depth': ['beginner', 'intermediate', 'expert'],
    'response_length_tendency': ['brief', 'moderate', 'verbose'],
    'question_asking_behavior': ['rarely', 'sometimes', 'frequently'],
    'riding_speed': ['slow', 'moderate', 'fast', 'very-fast'],
    'power_level': ['low', 'moderate', 'strong', 'elite'],
    'group_category': ['A', 'B', 'C'],
    'mind_games': ['none', 'subtle', 'moderate', 'expert'],
    'social_style': ['quiet', 'social', 'leader', 'entertainer'],
}

# All trait fields used for display and editing
ALL_TRAIT_FIELDS = [
    'tone', 'humor_type', 'directness', 'signature_phrases',
    'topic_biases', 'topics_allowed', 'response_length_tendency',
    'question_asking_behavior', 'encouragement_style', 'technical_depth',
    'domain_bias', 'riding_speed', 'power_level', 'group_category',
    'mind_games', 'social_style',
]

# Legacy alias
COACH_TRAIT_FIELDS = ALL_TRAIT_FIELDS

GEAR_ENUMS = {
    'bike_material': ['aluminum', 'steel', 'titanium', 'carbon', 'other'],
    'value_orientation': ['budget', 'mid-range', 'premium', 'buy-once-buy-right'],
}

GEAR_FIELDS = [
    'bike_make', 'bike_model', 'bike_year', 'bike_material',
    'wheels_tires', 'lighting', 'bags', 'navigation', 'kit',
    'value_orientation',
]

GUARDRAIL_ENUMS = {
    'rule_type': ['topic_block', 'tone_limit', 'escalation', 'scope'],
    'applies_to': ['all', 'shriram', 'venki'],
}


def _require_admin():
    """Check if current user is an admin. Aborts with 403 if not."""
    from routes.riders import is_admin_user
    if not is_admin_user():
        abort(403)


def _safe_admin_redirect(url):
    """Redirect to `url` only if it is a relative path on this host.

    Prevents open-redirect attacks via the `?next=` parameter.
    """
    from urllib.parse import urlparse
    if url:
        parsed = urlparse(url)
        if not parsed.scheme and not parsed.netloc:
            return redirect(url)
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if verify_password(password):
            session.permanent = True   # persist like the rider login (30d)
            session['logged_in'] = True
            next_url = request.args.get('next')
            return _safe_admin_redirect(next_url)
        else:
            return render_template('admin/login.html', error='Invalid password')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('main.index'))


@admin_bp.route('/')
@admin_bp.route('/season/<int:season_id>')
@user_login_required
def dashboard(season_id=None):
    _require_admin()
    from models import get_all_seasons, get_route_plan_operations_status, _execute

    all_seasons = get_all_seasons()
    current = get_current_season()

    if season_id:
        season = next((s for s in all_seasons if s['id'] == season_id), current)
    else:
        season = current

    rides = get_rides_with_signup_counts(season['id']) if season else []
    today = date.today()

    # Wind data status
    wind_status = None
    try:
        total_row = _execute(
            "SELECT COUNT(DISTINCT ride_id) as cnt FROM ride_wind_data"
        ).fetchone()
        missing_row = _execute("""
            SELECT COUNT(*) as cnt FROM ride r
            JOIN ride_plan rp ON r.ride_plan_id = rp.id
            JOIN season s ON r.season_id = s.id
            WHERE (s.is_current = true
                   OR s.name IN (SELECT name FROM season WHERE is_current = false ORDER BY name DESC LIMIT 1))
            AND r.date < CURRENT_DATE
            AND NOT EXISTS (SELECT 1 FROM ride_wind_data rwd WHERE rwd.ride_id = r.id)
        """).fetchone()
        wind_status = {
            'total': total_row['cnt'] if total_row else 0,
            'missing': missing_row['cnt'] if missing_row else 0,
        }
    except Exception:
        pass

    try:
        pipeline_status = route_plan_status(get_route_plan_operations_status())
    except Exception:
        current_app.logger.exception('Could not load route-plan pipeline status')
        pipeline_status = route_plan_status({})

    return render_template('admin/dashboard.html', season=season, rides=rides,
                           today=today, wind_status=wind_status,
                           pipeline_status=pipeline_status,
                           all_seasons=all_seasons)


@admin_bp.route('/strava')
@user_login_required
def strava_status():
    _require_admin()
    riders = get_strava_admin_summary()
    return render_template('admin/strava_status.html', riders=riders)


@admin_bp.route('/sync-strava/<int:rider_id>', methods=['POST'])
@user_login_required
def sync_strava_for_rider(rider_id):
    """Fetch the latest year of Strava activities for one rider."""
    _require_admin()
    from services.strava import sync_rider_activities

    connection = get_strava_connection(rider_id)
    if not connection:
        return jsonify({'error': 'No Strava connection for this rider'}), 404

    try:
        counts = sync_rider_activities(
            rider_id=rider_id,
            days=365,
            calculate_eddington=True,
        )
        refreshed = get_strava_connection(rider_id)
        return jsonify({
            'rider_id': rider_id,
            'new': counts.get('new', 0),
            'updated': counts.get('updated', 0),
            'failed': counts.get('failed', 0),
            'total': counts.get('total', 0),
            'eddington_miles': refreshed.get('eddington_number_miles', 0) if refreshed else 0,
            'eddington_km': refreshed.get('eddington_number_km', 0) if refreshed else 0,
        })
    except Exception as exc:
        current_app.logger.exception('Admin Strava sync failed for rider %s', rider_id)
        return jsonify({'error': str(exc)}), 500


@admin_bp.route('/backfill-strava/<int:rider_id>', methods=['POST'])
@user_login_required
def backfill_strava_for_rider(rider_id):
    """Run one forced 90-day historical Strava backfill chunk for a rider."""
    _require_admin()
    from routes.cron import _do_gradual_backfill

    connections = get_all_active_strava_connections()
    if not any(c['rider_id'] == rider_id for c in connections):
        return jsonify({'error': 'No active Strava connection for this rider'}), 404

    try:
        result = _do_gradual_backfill(connections, force_rider_id=rider_id)
        if result.get('error'):
            return jsonify(result), 502
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception('Admin Strava backfill failed for rider %s', rider_id)
        return jsonify({'error': str(exc)}), 500


@admin_bp.route('/sync-finish-times', methods=['POST'])
@user_login_required
def sync_finish_times():
    """Sync official finish times from RUSA for completed rides."""
    _require_admin()
    from models import sync_rusa_finish_times
    try:
        results = sync_rusa_finish_times()
        total_synced = sum(r.get('results_found', 0) for r in results)
        return jsonify({
            'synced': total_synced,
            'riders_checked': len(results),
            'details': results,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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


@admin_bp.route('/run-wind-backfill', methods=['POST'])
@user_login_required
def run_wind_backfill():
    """Trigger wind data backfill from admin dashboard."""
    _require_admin()

    import re
    import time
    from services.rwgps import fetch_route
    from services.weather import get_historical_stop_wind, wind_cell_style
    from models import _execute, get_db, get_ride_wind_data, get_ride_plan_stops

    conn = get_db()
    cur = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)

    # Find past rides with plans but no wind data (current + previous season)
    cur.execute("""
        SELECT r.id, r.name, r.date, r.distance_km,
               rp.id as plan_id, rp.slug as plan_slug,
               r.rwgps_url, r.rwgps_url_team
        FROM ride r
        JOIN ride_plan rp ON r.ride_plan_id = rp.id
        JOIN season s ON r.season_id = s.id
        WHERE (s.is_current = true
               OR s.name IN (SELECT name FROM season WHERE is_current = false ORDER BY name DESC LIMIT 1))
        AND r.date < CURRENT_DATE
        AND NOT EXISTS (SELECT 1 FROM ride_wind_data rwd WHERE rwd.ride_id = r.id)
        ORDER BY r.date DESC
        LIMIT 10
    """)
    rides = cur.fetchall()

    if not rides:
        return jsonify({'message': 'All rides have wind data', 'processed': 0, 'success': 0}), 200

    results = []
    for ride in rides:
        ride_result = {'ride_id': ride['id'], 'name': ride['name'], 'date': str(ride['date'])}
        try:
            rwgps_url = ride['rwgps_url_team'] or ride['rwgps_url']
            match = re.search(r'/routes/(\d+)', rwgps_url) if rwgps_url else None
            if not match:
                ride_result['status'] = 'skip_no_route'
                results.append(ride_result)
                continue

            route_id = int(match.group(1))
            route_data = fetch_route(route_id)
            track_points = route_data.get('track_points', []) if route_data else []

            if not track_points:
                ride_result['status'] = 'skip_no_track'
                results.append(ride_result)
                continue

            plan_stops = get_ride_plan_stops(ride['plan_id'])
            if not plan_stops:
                ride_result['status'] = 'skip_no_stops'
                results.append(ride_result)
                continue

            ride_date = ride['date']
            if isinstance(ride_date, str):
                ride_date = date.fromisoformat(ride_date)

            wind_rows, data_source = get_historical_stop_wind(
                stops=[dict(s) for s in plan_stops],
                track_points=track_points,
                ride_date=ride_date,
                ride_id=ride['id'],
            )

            if wind_rows:
                ride_result['status'] = 'ok'
                ride_result['stops'] = len(wind_rows)
                ride_result['source'] = data_source
            else:
                ride_result['status'] = 'no_data'
        except Exception as e:
            current_app.logger.exception("admin wind backfill: ride %s", ride['id'])
            ride_result['status'] = f'error: {str(e)[:80]}'

        results.append(ride_result)
        time.sleep(1)

    ok_count = sum(1 for r in results if r.get('status') == 'ok')
    return jsonify({'processed': len(results), 'success': ok_count, 'results': results}), 200


@admin_bp.route('/rides/<int:ride_id>/fetch-wind', methods=['POST'])
@user_login_required
def fetch_wind_for_ride(ride_id):
    """Fetch and store wind data for a specific ride from admin dashboard."""
    _require_admin()

    import re
    from services.rwgps import fetch_route
    from services.weather import get_historical_stop_wind
    import psycopg2.extras
    from models import get_ride_plan_stops, _execute, get_db

    cur = get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT r.id, r.name, r.date, r.ride_plan_id,
               r.rwgps_url, r.rwgps_url_team, rp.id as plan_id
        FROM ride r
        LEFT JOIN ride_plan rp ON r.ride_plan_id = rp.id
        WHERE r.id = %s
    """, (ride_id,))
    ride = cur.fetchone()

    if not ride:
        return jsonify({'error': 'Ride not found'}), 404
    if not ride['ride_plan_id']:
        return jsonify({'error': 'Ride has no linked ride plan — cannot fetch wind'}), 400

    rwgps_url = ride['rwgps_url_team'] or ride['rwgps_url']
    match = re.search(r'/routes/(\d+)', rwgps_url) if rwgps_url else None
    if not match:
        return jsonify({'error': 'Ride plan has no RWGPS route URL'}), 400

    try:
        route_data = fetch_route(int(match.group(1)))
        track_points = route_data.get('track_points', []) if route_data else []
        if not track_points:
            return jsonify({'error': 'No track points found in RWGPS route'}), 400

        plan_stops = get_ride_plan_stops(ride['plan_id'])
        if not plan_stops:
            return jsonify({'error': 'No stops found in ride plan'}), 400

        ride_date = ride['date']
        if isinstance(ride_date, str):
            ride_date = date.fromisoformat(ride_date)

        # Delete existing wind data first so we get a fresh fetch
        conn = get_db()
        conn.cursor().execute("DELETE FROM ride_wind_data WHERE ride_id = %s", (ride_id,))
        conn.commit()

        wind_rows, data_source = get_historical_stop_wind(
            stops=[dict(s) for s in plan_stops],
            track_points=track_points,
            ride_date=ride_date,
            ride_id=ride_id,
        )

        if wind_rows:
            return jsonify({
                'status': 'ok',
                'ride': ride['name'],
                'stops': len(wind_rows),
                'source': data_source,
            }), 200
        else:
            return jsonify({'error': 'No wind data returned from API (ride may be too recent or too old)'}), 400

    except Exception as e:
        current_app.logger.exception("fetch_wind_for_ride: ride %s", ride_id)
        return jsonify({'error': str(e)[:120]}), 500


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

    riders = get_all_riders()
    matrix = get_participation_matrix(ride['season_id']) if ride.get('season_id') else {}

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


@admin_bp.route('/rides/<int:ride_id>/edit', methods=['GET', 'POST'])
@user_login_required
def ride_edit(ride_id):
    """Edit ride details and waypoints."""
    _require_admin()
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    clubs = get_clubs()

    if request.method == 'POST':
        # Update core ride fields
        core_fields = {}
        for f in ('name', 'ride_type'):
            val = request.form.get(f, '').strip()
            if val:
                core_fields[f] = val
        ride_date = request.form.get('date', '').strip()
        if ride_date:
            core_fields['date'] = ride_date
        for f in ('distance_km', 'elevation_ft'):
            val = request.form.get(f, '').strip()
            core_fields[f] = int(val) if val else None
        for f in ('distance_miles', 'ft_per_mile'):
            val = request.form.get(f, '').strip()
            core_fields[f] = float(val) if val else None
        club_id = request.form.get('club_id', '').strip()
        core_fields['club_id'] = int(club_id) if club_id else None
        update_ride_core(ride_id, core_fields)

        # Update extended ride details
        start_time = request.form.get('start_time', '').strip()
        rwgps_url_team = request.form.get('rwgps_url_team', '').strip()
        update_ride_details(
            ride_id,
            rwgps_url=request.form.get('rwgps_url', ''),
            start_location=request.form.get('start_location', ''),
            time_limit_hours=float(request.form.get('time_limit_hours', '') or 0) or None,
            start_time=start_time if start_time else None,
            rwgps_url_team=rwgps_url_team if rwgps_url_team else None,
        )

        flash(f'Ride "{request.form.get("name", ride["name"])}" updated.', 'success')
        return redirect(url_for('admin.ride_edit', ride_id=ride_id))

    return render_template('admin/ride_edit.html', ride=ride, clubs=clubs)


@admin_bp.route('/rides/<int:ride_id>/remove-rider', methods=['POST'])
@user_login_required
def remove_rider_from_ride(ride_id):
    """Admin-only: remove a rider's participation record from a ride."""
    _require_admin()
    rider_id = request.form.get('rider_id', type=int)
    if not rider_id:
        flash('Missing rider ID.', 'error')
        return redirect(url_for('admin.mark_status', ride_id=ride_id))

    deleted = admin_delete_rider_ride(rider_id, ride_id)
    if deleted:
        flash('Rider participation removed.', 'success')
    else:
        flash('No participation record found to remove.', 'warning')
    return redirect(url_for('admin.mark_status', ride_id=ride_id))


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


@admin_bp.route('/recalculate-eddington')
@user_login_required
def recalculate_eddington():
    """Recalculate Eddington numbers for all riders with Strava connections."""
    _require_admin()
    from flask import jsonify
    from services.eddington import calculate_eddington_number

    connections = get_all_active_strava_connections()
    results = []
    for conn in connections:
        rid = conn['rider_id']
        name = conn['rider_name']
        activities = get_all_strava_activities_for_eddington(rid)
        miles = calculate_eddington_number(activities, unit='miles')
        km = calculate_eddington_number(activities, unit='km')
        update_eddington_number(rid, miles, km)
        results.append({
            'rider_id': rid,
            'name': name,
            'eddington_miles': miles,
            'eddington_km': km,
            'activities': len(activities),
        })

    return jsonify({
        'recalculated': len(results),
        'riders': results,
    })


@admin_bp.route('/resync-strava-range/<int:rider_id>')
@user_login_required
def resync_strava_range(rider_id):
    """Re-sync Strava activities for a specific date range.

    Query params:
        after: ISO date (e.g. 2013-01-01)
        before: ISO date (e.g. 2018-01-01)
    """
    _require_admin()
    from flask import jsonify
    from datetime import datetime
    from services.strava import sync_rider_activities

    after_str = request.args.get('after', '2013-01-01')
    before_str = request.args.get('before', '2018-01-01')

    after_epoch = int(datetime.fromisoformat(after_str).timestamp())
    before_epoch = int(datetime.fromisoformat(before_str).timestamp())

    counts = sync_rider_activities(
        rider_id=rider_id,
        after_epoch=after_epoch,
        before_epoch=before_epoch,
        calculate_eddington=True,
    )

    return jsonify({
        'rider_id': rider_id,
        'range': {'after': after_str, 'before': before_str},
        'results': counts,
    })


# ── Personality Admin ────────────────────────────────────────────────

def compute_completeness(profile):
    """Count non-null/non-empty trait fields. Returns (filled, total, confidence)."""
    if not profile:
        return (0, len(COACH_TRAIT_FIELDS), None)
    filled = 0
    for field in COACH_TRAIT_FIELDS:
        val = profile.get(field)
        if val is not None and val != '' and val != []:
            filled += 1
    confidence = profile.get('extraction_confidence') if profile else None
    return (filled, len(COACH_TRAIT_FIELDS), confidence)


@admin_bp.route('/personalities')
@user_login_required
def personalities():
    """List all riders with personality profile completeness indicators."""
    _require_admin()
    riders = get_all_riders()
    # Load both coach and rider profiles — show whichever exists for each person
    all_profiles = get_all_personality_profiles(profile_type=None)

    # Build profiles dict keyed by rider_id, preferring: coach > rider, then merged > manual > whatsapp
    profiles = {}
    type_priority = {'coach': 0, 'rider': 1}
    source_priority = {'merged': 0, 'manual': 1, 'whatsapp': 2, 'blog': 3}
    for p in all_profiles:
        rid = p['rider_id']
        existing = profiles.get(rid)
        if existing is None:
            profiles[rid] = p
        else:
            cur_type_pri = type_priority.get(existing.get('profile_type', ''), 99)
            new_type_pri = type_priority.get(p.get('profile_type', ''), 99)
            if new_type_pri < cur_type_pri:
                profiles[rid] = p
            elif new_type_pri == cur_type_pri:
                cur_src_pri = source_priority.get(existing.get('extraction_source', ''), 99)
                new_src_pri = source_priority.get(p.get('extraction_source', ''), 99)
                if new_src_pri < cur_src_pri:
                    profiles[rid] = p

    completeness = {}
    for r in riders:
        completeness[r['id']] = compute_completeness(profiles.get(r['id']))

    return render_template('admin/personalities.html',
                           riders=riders, profiles=profiles,
                           completeness=completeness)


@admin_bp.route('/personalities/<int:rider_id>', methods=['GET', 'POST'])
@user_login_required
def personality_edit(rider_id):
    """View or edit personality traits for a single rider."""
    _require_admin()
    rider = get_rider_by_id(rider_id)
    if not rider:
        abort(404)

    if request.method == 'POST':
        fields = {}
        for trait in COACH_TRAIT_FIELDS:
            val = request.form.get(trait, '').strip()
            if trait in ('signature_phrases', 'topic_biases', 'topics_allowed'):
                # Newline-separated textarea -> list
                lines = [line.strip() for line in val.split('\n') if line.strip()]
                fields[trait] = lines if lines else None
            elif trait in PERSONALITY_ENUMS:
                fields[trait] = val if val else None
            else:
                fields[trait] = val if val else None
        fields['extraction_source'] = 'manual'
        upsert_personality_profile(rider_id, 'coach', fields, updated_by='admin')
        flash('Personality profile saved.', 'success')
        return redirect(url_for('admin.personality_edit', rider_id=rider_id))

    # GET: load profile preferring merged > manual > whatsapp, checking both coach and rider types
    profile = None
    from models import _execute
    for ptype in ('coach', 'rider'):
        for source in ('merged', 'manual', 'whatsapp', 'blog'):
            row = _execute(
                """SELECT * FROM personality_profile
                   WHERE rider_id = %s AND profile_type = %s
                   AND extraction_source = %s AND deleted_at IS NULL""",
                (rider_id, ptype, source)
            ).fetchone()
            if row:
                profile = row
                break
        if profile:
            break
    if not profile:
        profile = get_personality_profile(rider_id, 'coach')
        if not profile:
            profile = get_personality_profile(rider_id, 'rider')

    evidence = get_trait_evidence(rider_id)
    # Group evidence by trait_name
    evidence_by_trait = {}
    for ev in evidence:
        trait = ev['trait_name']
        if trait not in evidence_by_trait:
            evidence_by_trait[trait] = []
        evidence_by_trait[trait].append(ev)

    return render_template('admin/personality_edit.html',
                           rider=rider, profile=profile,
                           evidence_by_trait=evidence_by_trait,
                           PERSONALITY_ENUMS=PERSONALITY_ENUMS,
                           COACH_TRAIT_FIELDS=COACH_TRAIT_FIELDS)


# ── Gear Admin ───────────────────────────────────────────────────────

@admin_bp.route('/gear')
@user_login_required
def gear():
    """List all riders with gear preference status."""
    _require_admin()
    riders = get_all_riders()
    from models import get_all_gear_for_rider
    gear_map = {}       # rider_id -> primary gear row
    bike_counts = {}    # rider_id -> total bike count
    all_bikes = {}      # rider_id -> list of all gear rows
    for r in riders:
        bikes = get_all_gear_for_rider(r['id'])
        gear_map[r['id']] = bikes[0] if bikes else None
        bike_counts[r['id']] = len(bikes)
        all_bikes[r['id']] = bikes
    return render_template('admin/gear.html', riders=riders, gear_map=gear_map,
                           bike_counts=bike_counts, all_bikes=all_bikes,
                           GEAR_FIELDS=GEAR_FIELDS)


@admin_bp.route('/gear/<int:rider_id>', methods=['GET', 'POST'])
@user_login_required
def gear_edit(rider_id):
    """View or edit gear preferences for a single rider (multiple bikes)."""
    _require_admin()
    rider = get_rider_by_id(rider_id)
    if not rider:
        abort(404)

    from models import get_all_gear_for_rider

    if request.method == 'POST':
        label = request.form.get('label', 'Primary').strip() or 'Primary'
        fields = {}
        for field in GEAR_FIELDS:
            val = request.form.get(field, '').strip()
            if field == 'bike_year':
                fields[field] = int(val) if val else None
            elif field in GEAR_ENUMS:
                fields[field] = val if val else None
            else:
                fields[field] = val if val else None
        upsert_gear_preference(rider_id, fields, updated_by='admin', label=label)
        flash(f'Gear saved for {label} bike.', 'success')
        return redirect(url_for('admin.gear_edit', rider_id=rider_id, bike=label))

    bikes = get_all_gear_for_rider(rider_id)
    selected_label = request.args.get('bike', 'Primary')
    gear = None
    for b in bikes:
        if b.get('label') == selected_label:
            gear = b
            break
    if not gear and bikes:
        gear = bikes[0]
        selected_label = gear.get('label', 'Primary')

    return render_template('admin/gear_edit.html', rider=rider, gear=gear,
                           bikes=bikes, selected_label=selected_label,
                           GEAR_ENUMS=GEAR_ENUMS, GEAR_FIELDS=GEAR_FIELDS)


# ── Coach Admin ──────────────────────────────────────────────────────

@admin_bp.route('/coaches')
@user_login_required
def coaches():
    """Coach roster with domain assignments and persona status."""
    _require_admin()
    assignments = get_coach_assignments(active_only=False)
    profiles = get_all_personality_profiles(profile_type='coach')
    profile_by_rider = {p['rider_id']: p for p in profiles}

    # Group assignments by coach_rider_id
    coaches_map = {}
    for a in assignments:
        cid = a['coach_rider_id']
        if cid not in coaches_map:
            rider = get_rider_by_id(cid)
            coaches_map[cid] = {
                'rider': rider,
                'has_profile': cid in profile_by_rider,
                'is_default': False,
                'assignments': [],
            }
        coaches_map[cid]['assignments'].append(a)
        if a.get('is_default'):
            coaches_map[cid]['is_default'] = True

    return render_template('admin/coaches.html', coaches=coaches_map)


@admin_bp.route('/coaches/add', methods=['GET', 'POST'])
@user_login_required
def add_coach():
    """Add a new coach by selecting an existing rider and assigning topic domains."""
    _require_admin()
    if request.method == 'POST':
        rider_id = request.form.get('rider_id', type=int)
        domains = request.form.get('domains', '').strip()
        is_default = request.form.get('is_default') == 'on'
        if not rider_id or not domains:
            flash('Rider and at least one domain are required.', 'error')
            return redirect(url_for('admin.add_coach'))
        # Create coach profile if needed
        existing = get_personality_profile(rider_id, 'coach')
        if not existing:
            upsert_personality_profile(rider_id, 'coach', {
                'extraction_source': 'manual',
            }, updated_by='admin')
        # Create assignments for each domain
        for domain in [d.strip() for d in domains.split(',') if d.strip()]:
            upsert_coach_assignment(rider_id, domain,
                                    {'is_active': True, 'is_default': is_default},
                                    updated_by='admin')
        flash(f'Coach added with domains: {domains}', 'success')
        return redirect(url_for('admin.coaches'))
    # GET: show form with all riders
    riders = get_all_riders()
    return render_template('admin/add_coach.html', riders=riders)


@admin_bp.route('/coaches/<int:coach_rider_id>/<topic_domain>/toggle', methods=['POST'])
@user_login_required
def toggle_coach(coach_rider_id, topic_domain):
    """Toggle active/inactive on a coach assignment."""
    _require_admin()
    assignments = get_coach_assignments(
        coach_rider_id=coach_rider_id, topic_domain=topic_domain, active_only=False)
    if not assignments:
        flash('Coach assignment not found.', 'error')
        return redirect(url_for('admin.coaches'))
    current_active = assignments[0].get('is_active', True)
    upsert_coach_assignment(coach_rider_id, topic_domain,
                            {'is_active': not current_active}, updated_by='admin')
    flash('Coach assignment toggled.', 'success')
    return redirect(url_for('admin.coaches'))


# ── Guardrail Admin ──────────────────────────────────────────────────

@admin_bp.route('/guardrails')
@user_login_required
def guardrails():
    """List all guardrail rules (active and inactive)."""
    _require_admin()
    rules = get_all_guardrails()
    return render_template('admin/guardrails.html', guardrails=rules,
                           GUARDRAIL_ENUMS=GUARDRAIL_ENUMS)


@admin_bp.route('/guardrails/<int:guardrail_id>/toggle', methods=['POST'])
@user_login_required
def toggle_guardrail(guardrail_id):
    """Toggle a guardrail rule active/inactive."""
    _require_admin()
    rules = get_all_guardrails()
    target = None
    for r in rules:
        if r['id'] == guardrail_id:
            target = r
            break
    if not target:
        flash('Guardrail not found.', 'error')
        return redirect(url_for('admin.guardrails'))
    update_guardrail(guardrail_id, {'is_active': not target['is_active']},
                     updated_by='admin')
    flash('Guardrail toggled.', 'success')
    return redirect(url_for('admin.guardrails'))


@admin_bp.route('/guardrails/new', methods=['GET', 'POST'])
@user_login_required
def guardrail_new():
    """Create a new guardrail rule."""
    _require_admin()
    if request.method == 'POST':
        rule_type = request.form.get('rule_type', '').strip()
        rule_value = request.form.get('rule_value', '').strip()
        applies_to = request.form.get('applies_to', 'all').strip()
        if not rule_type or not rule_value:
            flash('Rule type and value are required.', 'error')
            return redirect(url_for('admin.guardrail_new'))
        insert_guardrail(rule_type, rule_value, applies_to, updated_by='admin')
        flash('Guardrail created.', 'success')
        return redirect(url_for('admin.guardrails'))
    return render_template('admin/guardrail_edit.html', guardrail=None,
                           GUARDRAIL_ENUMS=GUARDRAIL_ENUMS)


@admin_bp.route('/guardrails/<int:guardrail_id>/edit', methods=['GET', 'POST'])
@user_login_required
def guardrail_edit(guardrail_id):
    """Edit an existing guardrail rule."""
    _require_admin()
    rules = get_all_guardrails()
    target = None
    for r in rules:
        if r['id'] == guardrail_id:
            target = r
            break
    if not target:
        flash('Guardrail not found.', 'error')
        return redirect(url_for('admin.guardrails'))

    if request.method == 'POST':
        fields = {
            'rule_type': request.form.get('rule_type', '').strip(),
            'rule_value': request.form.get('rule_value', '').strip(),
            'applies_to': request.form.get('applies_to', 'all').strip(),
        }
        update_guardrail(guardrail_id, fields, updated_by='admin')
        flash('Guardrail updated.', 'success')
        return redirect(url_for('admin.guardrails'))

    return render_template('admin/guardrail_edit.html', guardrail=target,
                           GUARDRAIL_ENUMS=GUARDRAIL_ENUMS)


@admin_bp.route('/guardrails/<int:guardrail_id>/delete', methods=['POST'])
@user_login_required
def guardrail_delete(guardrail_id):
    """Soft-delete a guardrail rule."""
    _require_admin()
    soft_delete_guardrail(guardrail_id, updated_by='admin')
    flash('Guardrail deleted.', 'success')
    return redirect(url_for('admin.guardrails'))


# ---------------------------------------------------------------------------
# Knowledge Base Admin (Phase 12)
# ---------------------------------------------------------------------------

@admin_bp.route('/knowledge')
@user_login_required
def knowledge():
    """List all embedded web sources with chunk counts and dates."""
    _require_admin()
    from models import get_knowledge_sources
    sources = get_knowledge_sources()
    return render_template('admin/knowledge.html', sources=sources)


@admin_bp.route('/knowledge/<path:source>/remove', methods=['POST'])
@user_login_required
def knowledge_remove(source):
    """Remove all embeddings for a web source."""
    _require_admin()
    if not source.startswith('web_'):
        flash('Can only remove web_ sources.', 'error')
        return redirect(url_for('admin.knowledge'))
    from models import delete_knowledge_source
    count = delete_knowledge_source(source)
    flash(f'Removed {count} chunks from {source}.', 'success')
    return redirect(url_for('admin.knowledge'))


@admin_bp.route('/knowledge/<path:source>/re-embed', methods=['POST'])
@user_login_required
def knowledge_reembed(source):
    """Clear embeddings for a source and show CLI re-embed instruction."""
    _require_admin()
    if not source.startswith('web_'):
        flash('Can only re-embed web_ sources.', 'error')
        return redirect(url_for('admin.knowledge'))
    from models import delete_knowledge_source
    count = delete_knowledge_source(source)
    flash(f'Cleared {count} chunks from {source}. Run: python scripts/embed_resources.py --source {source}', 'info')
    return redirect(url_for('admin.knowledge'))


@admin_bp.route('/refresh-rusa-events', methods=['POST'])
@user_login_required
def refresh_rusa_events():
    """Trigger RUSA event calendar refresh from admin dashboard."""
    _require_admin()
    import subprocess
    import sys

    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scripts', 'update_rusa_events.py')

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=120,
            env={**os.environ}
        )
        output = result.stdout + result.stderr

        # Parse stats from output
        import re
        stats_match = re.search(r'(\d+) inserted, (\d+) updated, (\d+) skipped', output)
        if stats_match:
            inserted, updated, skipped = stats_match.groups()
            return jsonify({
                'success': True,
                'inserted': int(inserted),
                'updated': int(updated),
                'skipped': int(skipped),
                'output': output,
            })
        elif result.returncode != 0:
            return jsonify({'error': f'Script failed: {output}'}), 500
        else:
            return jsonify({'success': True, 'output': output})

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Script timed out after 120s'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/rides/<int:ride_id>/detail')
def ride_detail(ride_id):
    """Ride detail page — finishers, finish times, plan links, weather. Public."""
    from models import get_ride_by_id, get_ride_participants, _execute
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    participants = get_ride_participants(ride_id)

    plan = None
    if ride.get('ride_plan_id'):
        plan = _execute("SELECT * FROM ride_plan WHERE id = %s", (ride['ride_plan_id'],)).fetchone()

    from routes.riders import is_admin_user
    can_edit = is_admin_user()

    return render_template('admin/ride_detail.html',
                           ride=ride, participants=participants,
                           plan=plan, can_edit=can_edit)


@admin_bp.route('/users')
@user_login_required
def admin_users():
    """Manage admin users."""
    _require_admin()
    from models import _execute
    users = _execute("""
        SELECT au.id, au.email, au.is_admin, au.last_login,
               r.first_name, r.last_name, r.rusa_id
        FROM app_user au
        LEFT JOIN rider r ON au.rider_id = r.id
        ORDER BY au.is_admin DESC, r.first_name NULLS LAST
    """).fetchall()
    return render_template('admin/users.html', users=users)


@admin_bp.route('/users/<int:user_id>/toggle-admin', methods=['POST'])
@user_login_required
def toggle_admin(user_id):
    """Toggle admin status for a user."""
    _require_admin()
    from models import get_db
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE app_user SET is_admin = NOT is_admin WHERE id = %s", (user_id,))
    conn.commit()
    return redirect(url_for('admin.admin_users'))
