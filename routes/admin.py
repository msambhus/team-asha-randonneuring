"""Admin routes: login, dashboard, ride entry, status marking, RWGPS plan generation."""
import json
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from models import (get_current_season, get_rides_for_season, get_riders_for_season,
                    get_ride_by_id, get_participation_matrix, get_clubs,
                    create_ride, update_rider_ride_status, get_all_riders,
                    get_ride_plan_by_rwgps_route_id, create_ride_plan_from_rwgps,
                    auto_finalize_past_rides, get_rides_with_signup_counts,
                    get_strava_admin_summary, get_all_active_strava_connections,
                    get_all_strava_activities_for_eddington, update_eddington_number,
                    admin_delete_rider_ride,
                    get_all_personality_profiles, get_personality_profile,
                    upsert_personality_profile, get_trait_evidence,
                    get_rider_by_id,
                    get_gear_preference, upsert_gear_preference,
                    get_coach_assignments, upsert_coach_assignment,
                    get_all_guardrails, insert_guardrail,
                    update_guardrail, soft_delete_guardrail)
from auth import login_required, user_login_required, verify_password
from services.rwgps import (extract_rwgps_route_id, fetch_route, extract_controls,
                            build_ride_plan, slugify)

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
    gear_map = {}
    for r in riders:
        g = get_gear_preference(r['id'])
        gear_map[r['id']] = g
    return render_template('admin/gear.html', riders=riders, gear_map=gear_map,
                           GEAR_FIELDS=GEAR_FIELDS)


@admin_bp.route('/gear/<int:rider_id>', methods=['GET', 'POST'])
@user_login_required
def gear_edit(rider_id):
    """View or edit gear preferences for a single rider."""
    _require_admin()
    rider = get_rider_by_id(rider_id)
    if not rider:
        abort(404)

    if request.method == 'POST':
        fields = {}
        for field in GEAR_FIELDS:
            val = request.form.get(field, '').strip()
            if field == 'bike_year':
                fields[field] = int(val) if val else None
            elif field in GEAR_ENUMS:
                fields[field] = val if val else None
            else:
                fields[field] = val if val else None
        upsert_gear_preference(rider_id, fields, updated_by='admin')
        flash('Gear preferences saved.', 'success')
        return redirect(url_for('admin.gear_edit', rider_id=rider_id))

    gear = get_gear_preference(rider_id)
    return render_template('admin/gear_edit.html', rider=rider, gear=gear,
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
