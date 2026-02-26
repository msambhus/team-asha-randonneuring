"""Rider routes: season view, individual profiles, profile edit, upcoming brevets, ride plans."""
from flask import Blueprint, render_template, abort, request, redirect, url_for, session, jsonify

def is_admin_user():
    """Check if current logged-in user is an admin."""
    user_id = session.get('user_id')
    if not user_id:
        return False
    
    from models import get_user_by_id, _execute
    user = get_user_by_id(user_id)
    if not user or not user.get('rider_id'):
        return False
    
    rider = _execute("SELECT first_name FROM rider WHERE id = %s", (user['rider_id'],)).fetchone()
    if not rider:
        return False
    
    allowed_names = ['sriharsha', 'venkatesh', 'mihir']
    return rider.get('first_name', '').lower() in allowed_names
from models import (get_season_by_name, get_riders_for_season, get_active_riders_for_season,
                    get_rides_for_season, get_participation_matrix, get_season_stats,
                    get_rider_by_rusa, get_rider_participation, get_rider_career_stats,
                    get_rider_season_stats, get_all_seasons, get_current_season,
                    detect_sr_for_rider_season, get_rider_total_srs,
                    get_all_rider_season_stats, detect_sr_for_all_riders_in_season,
                    get_upcoming_rusa_events, update_rider_profile, update_strava_privacy,
                    get_pbp_finishers,
                    get_all_ride_plans, get_ride_plan_by_slug, get_ride_plan_stops,
                    get_signup_count, get_rider_signup_status, get_ride_by_id, update_ride_details,
                    get_user_by_id, _execute,
                    get_strava_connection, get_strava_activities,
                    get_rider_upcoming_signups, detect_r12_awards,
                    get_signup_counts_batch, get_rider_signup_statuses_batch,
                    get_custom_plan, get_custom_plan_by_id, create_custom_plan,
                    get_custom_plan_stops_raw, update_custom_plan_stop,
                    add_custom_stop, hide_base_stop, unhide_base_stop,
                    update_custom_plan_settings, delete_custom_plan,
                    get_public_custom_plans, clone_custom_plan, delete_custom_stop)
from auth import login_required, user_login_required
from services.fitness import (calculate_fitness_score, score_all_activities,
                              assess_readiness, generate_training_advice)
from services.openai_coach import generate_openai_advice
from services.custom_plan_service import (get_merged_plan_stops, 
                                          recalculate_cumulative_values,
                                          apply_pace_adjustment, compare_plans)
from cache import cache, CACHE_TIMEOUT
from datetime import date, datetime, timedelta
import re

riders_bp = Blueprint('riders', __name__)

# Map season name to display label
SEASON_LABELS = {
    '2025-2026': '2025/2026 Season',
    '2022-2023': '2022-2023 Season',
    '2021-2022': '2021-2022 Season',
}


@riders_bp.route('/riders/<season_name>')
@cache.cached(timeout=CACHE_TIMEOUT)
def season_riders(season_name):
    try:
        season = get_season_by_name(season_name)
        if not season:
            abort(404)

        riders_all = get_riders_for_season(season['id'])
        rides = get_rides_for_season(season['id'])
        matrix = get_participation_matrix(season['id'])
        current = get_current_season()
        is_current = current and current['id'] == season['id']

        # For current season, only count past rides in stats
        stats = get_season_stats(season['id'], past_only=is_current)

        today = date.today()
        past_rides = [r for r in rides if r['date'] and r['date'] <= today]

        # Only show riders who have completed at least 1 brevet (past rides only)
        if is_current:
            riders = get_active_riders_for_season(season['id'])
        else:
            riders = riders_all

        # Batch-fetch per-rider stats (2 queries instead of 34)
        all_stats = get_all_rider_season_stats(season['id'])
        all_srs = detect_sr_for_all_riders_in_season(season['id'], date_filter=is_current)

        # Compute per-rider stats for display
        rider_data = []
        for r in riders:
            s = all_stats.get(r['id'], {'rides': 0, 'kms': 0})
            sr_n = all_srs.get(r['id'], 0)
            rides_count = s['rides']
            kms_count = s['kms']

            # For current season, only count past ride completions
            if is_current:
                past_ride_ids = {pr['id'] for pr in past_rides}
                part = matrix.get(r['id'], {})
                rides_count = sum(1 for rid, p in part.items()
                                 if rid in past_ride_ids and p['status'] == 'FINISHED')
                kms_count = sum(ri['distance_km'] for ri in past_rides
                               if ri['id'] in part and part[ri['id']]['status'] == 'FINISHED')

            if rides_count > 0 or not is_current:
                rider_data.append({
                    'rider': r,
                    'rides': rides_count,
                    'kms': kms_count,
                    'sr_count': sr_n,
                    'participation': matrix.get(r['id'], {}),
                })

        # Sort by first name ascending (default), then last name
        rider_data.sort(key=lambda x: (x['rider']['first_name'].lower(), x['rider']['last_name'].lower()))

        # Filter past_rides to only those with at least one finisher/OTL among displayed riders
        displayed_rider_ids = {rd['rider']['id'] for rd in rider_data}
        past_rides = [r for r in past_rides if any(
            matrix.get(rid, {}).get(r['id'], {}).get('status') in ('FINISHED', 'OTL')
            for rid in displayed_rider_ids
        )]

        label = SEASON_LABELS.get(season_name, f'{season_name} Season')

        # Get upcoming event count for the summary box
        upcoming_count = 0
        if is_current:
            rusa_events = get_upcoming_rusa_events()
            upcoming_count = len(rusa_events)

        # PBP finishers for seasons that had PBP
        pbp_finishers = get_pbp_finishers(season['id']) if not is_current else []

        return render_template('riders.html',
                               season=season,
                               season_label=label,
                               riders=rider_data,
                               past_rides=past_rides,
                               stats=stats,
                               is_current=is_current,
                               upcoming_count=upcoming_count,
                               pbp_finishers=pbp_finishers)
    except Exception as e:
        # Return mock data for testing without database
        print(f"Database not available for riders page, using mock data: {e}")
        mock_stats = {
            'active_riders': 25,
            'total_rides': 48,
            'total_kms': 28500,
            'sr_count': 5,
            'sr_rider_count': 8
        }
        return render_template('riders.html',
                               season={'id': 3, 'name': season_name},
                               season_label=SEASON_LABELS.get(season_name, f'{season_name} Season'),
                               riders=[],
                               past_rides=[],
                               stats=mock_stats,
                               is_current=True,
                               upcoming_count=12,
                               pbp_finishers=[])


def _normalize_route(name):
    """Normalize a route name for matching: lowercase, strip common suffixes."""
    s = name.lower()
    s = re.sub(r'&nbsp;', ' ', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\b(plan|route|brevet|k|km|mi)\b', '', s)
    s = re.sub(r'\b(20\d{2})\b', '', s)  # remove years
    s = re.sub(r'#\d+', '', s)  # remove brevet numbers
    return set(s.split()) - {'', 'the', 'a', 'and', 'of', 'in', 'to', 'scr', 'sfr', 'dbc', 'sr', 'ta'}


# Words too generic for single-word matching
_GENERIC_WORDS = {'200', '300', '302', '400', '600', '1000', '1200',
                  '200k', '300k', '400k', '600k', '1000k', '1200k',
                  'city', 'lake', 'valley', 'creek', 'mountain', 'mountains',
                  'coast', 'bay', 'point', 'beach', 'night', 'gold', 'river',
                  'davis', 'del', 'san'}


def _extract_distance_km(name):
    """Extract brevet distance class in km from plan name.
    E.g., 'SFR 300k Healdsburg' -> 300, 'Davis 200K' -> 200."""
    match = re.search(r'(\d{3,4})\s*[kK]', name)
    return int(match.group(1)) if match else None


_CUTOFF_HOURS = {200: 13.5, 300: 20, 400: 27, 600: 40, 1000: 75, 1200: 90}


def _get_cutoff_hours(km):
    """Standard ACP/RUSA time limits by distance class."""
    if not km:
        return None
    for limit in sorted(_CUTOFF_HOURS):
        if km <= limit:
            return _CUTOFF_HOURS[limit]
    return None


def _compute_difficulty_score(ft_per_mi, notes):
    """Difficulty score 0-10. Base from ft/mile, modifiers from notes keywords."""
    if not ft_per_mi:
        return 0.0
    base = min(ft_per_mi / 10.0, 7.0)
    if notes:
        n = notes.lower()
        if 'headwind' in n:
            base += 1.5
        if 'steep' in n or 'steep climb' in n:
            base += 1.0
        if 'exposed' in n or 'gravel' in n:
            base += 0.5
        if 'tailwind' in n:
            base -= 0.5
    return round(min(max(base, 0), 10), 1)


def _difficulty_label(score):
    """Convert numeric difficulty score to label."""
    if score >= 7:
        return 'hard'
    if score >= 4:
        return 'moderate'
    if score >= 1.5:
        return 'easy'
    return 'flat'


_DIFFICULTY_COLORS = {
    'hard': '#ef4444',
    'moderate': '#f59e0b',
    'easy': '#22c55e',
    'flat': '#94a3b8',
}


def _difficulty_color(ft_per_mi):
    """Return a hex color from a continuous gradient based on ft/mile.
    Anchor points: 0=#94a3b8 (slate), 25=#22c55e (green), 50=#f59e0b (amber),
    75=#ef4444 (red), 100=#991b1b (dark red). Centered on 50 ft/mi = moderate."""
    if not ft_per_mi or ft_per_mi <= 0:
        return '#94a3b8'

    anchors = [
        (0,   (0x94, 0xa3, 0xb8)),   # slate gray
        (25,  (0x22, 0xc5, 0x5e)),   # green
        (50,  (0xf5, 0x9e, 0x0b)),   # amber
        (75,  (0xef, 0x44, 0x44)),   # red
        (100, (0x99, 0x1b, 0x1b)),   # dark red
    ]

    if ft_per_mi >= 100:
        return '#991b1b'

    for i in range(len(anchors) - 1):
        lo_val, lo_rgb = anchors[i]
        hi_val, hi_rgb = anchors[i + 1]
        if lo_val <= ft_per_mi <= hi_val:
            t = (ft_per_mi - lo_val) / (hi_val - lo_val)
            r = int(lo_rgb[0] + t * (hi_rgb[0] - lo_rgb[0]))
            g = int(lo_rgb[1] + t * (hi_rgb[1] - lo_rgb[1]))
            b = int(lo_rgb[2] + t * (hi_rgb[2] - lo_rgb[2]))
            return '#{:02x}{:02x}{:02x}'.format(r, g, b)

    return '#94a3b8'


def _extract_rwgps_route_id(url):
    """Extract numeric route ID from a RWGPS URL."""
    if not url:
        return None
    m = re.search(r'/routes/(\d+)', url)
    return m.group(1) if m else None


def _build_journey_nodes(stops):
    """Collapse stops at same distance into single nodes for the journey strip.
    When a rest stop shares the same distance as the previous waypoint,
    label becomes 'Rest activity @ Previous location' (e.g. 'Water refill @ Fire station')."""
    nodes = []
    for s in stops:
        if nodes and nodes[-1]['distance_miles'] == (s.get('distance_miles') or 0):
            existing = nodes[-1]
            if s['stop_type'] in ('rest', 'control'):
                # "Water refill @ Fire station" — rest location @ previous node's label
                existing['label'] = "{} @ {}".format(s['location'][:18], existing['label'][:18])
                if s['stop_type'] == 'control':
                    existing['node_type'] = 'control'
                elif existing['node_type'] == 'waypoint':
                    existing['node_type'] = s['stop_type']
            # Merge difficulty: take the harder one
            if s.get('difficulty_score', 0) > existing.get('difficulty_score', 0):
                existing['difficulty_score'] = s['difficulty_score']
                existing['difficulty_label'] = s.get('difficulty_label', 'flat')
                existing['difficulty_color'] = s.get('difficulty_color', '#94a3b8')
            # Merge cum_time: take the max (rest adds break time)
            if s.get('cum_time_min', 0) > existing.get('cum_time_min', 0):
                existing['cum_time_min'] = s['cum_time_min']
            # Keep arrival time (should be the same for co-located stops)
            if s.get('arrival_time_min') is not None:
                existing['arrival_time_min'] = s['arrival_time_min']
        else:
            # Use stop_name if available, otherwise fallback to location
            label = s.get('stop_name') or s['location'][:22]
            if s['stop_type'] == 'rest' and not s.get('stop_name'):
                label = "Rest @ {}".format(s['location'][:18])
            nodes.append({
                'label': label,
                'distance_miles': s.get('distance_miles') or 0,
                'node_type': s['stop_type'],
                'arrival_time_min': s.get('arrival_time_min'),
                'difficulty_score': s.get('difficulty_score', 0),
                'difficulty_label': s.get('difficulty_label', 'flat'),
                'difficulty_color': s.get('difficulty_color', '#94a3b8'),
                'cum_time_min': s.get('cum_time_min', 0),
                'stop_name': s.get('stop_name'),
                'stop_duration_min': s.get('stop_duration_min', 0),
            })
    return nodes


def _attach_break_metadata(stops):
    """Attach break metadata for the timeline layout.

    Now that rest stops are integrated into stops via stop_name and stop_duration_min,
    this function simply marks stops as having break info if they have stop_name/duration.
    Returns (stops, use_timeline) — use_timeline is False if stop types
    are missing/ambiguous, signaling templates to use the original flat view.
    """
    KNOWN_TYPES = {'start', 'finish', 'control', 'rest', 'waypoint'}
    # Check if stops have identifiable stop_type values
    types_found = {s.get('stop_type') for s in stops if s.get('stop_type')}
    if not types_found or not types_found & KNOWN_TYPES:
        return stops, False

    # Require first stop to be 'start' for timeline layout
    if stops and stops[0].get('stop_type') != 'start':
        return stops, False

    # Mark stops with break info (stop_name and stop_duration_min)
    for stop in stops:
        stop['_is_merged_break'] = False
        stop['_has_break'] = bool(stop.get('stop_name') and stop.get('stop_duration_min'))

    return stops, True


def _match_plans_to_events(events, plans):
    """Attach plan_slug and Team Asha route URLs to RUSA events by matching route names.
    Requires at least 2 meaningful keyword matches to avoid false positives,
    unless there's a distinctive word match (e.g. 'healdsburg', 'hopland')."""
    for event in events:
        e_words = _normalize_route(event.get('route_name', ''))
        best_slug = None
        best_plan = None
        best_score = 0
        for plan in plans:
            p_words = _normalize_route(plan['name'])
            common = e_words & p_words
            distinctive = common - _GENERIC_WORDS
            # Need at least 1 distinctive word, or 2+ common words with at least one non-generic
            if len(distinctive) >= 1 and len(common) >= 2:
                score = len(common) + len(distinctive)
                if score > best_score:
                    best_score = score
                    best_slug = plan['slug']
                    best_plan = plan
        event['plan_slug'] = best_slug
        if best_plan:
            event['plan_rwgps_url'] = best_plan.get('rwgps_url')
            event['plan_rwgps_url_team'] = best_plan.get('rwgps_url_team')


@riders_bp.route('/riders/<season_name>/upcoming')
def upcoming_brevets(season_name):
    from flask import session
    from models import get_user_by_id
    
    season = get_season_by_name(season_name)
    if not season:
        abort(404)

    current = get_current_season()
    is_current = current and current['id'] == season['id']
    if not is_current:
        return redirect(url_for('riders.season_riders', season_name=season_name))

    rusa_events = get_upcoming_rusa_events()

    rides = get_rides_for_season(season['id'])
    today = date.today()
    future_rides = [r for r in rides if r['date'] and r['date'] > today]

    # Build ride plan lookup for RUSA events
    plans = get_all_ride_plans()
    _match_plans_to_events(rusa_events, plans)

    # Get current user's rider_id and signup statuses
    rider_id = None
    current_rider = None
    user_signups = {}
    user_custom_plans = {}
    can_edit_rides = False
    user_id = session.get('user_id')
    
    # Batch load signup counts for all events (1 query instead of N queries)
    ride_ids = [e['id'] for e in rusa_events]
    signup_counts = get_signup_counts_batch(ride_ids)
    
    if user_id:
        user = get_user_by_id(user_id)
        if user and user.get('rider_id'):
            rider_id = user['rider_id']
            # Fetch rider details using rider_id
            current_rider = _execute("SELECT * FROM rider WHERE id = %s", (rider_id,)).fetchone()
            
            # Check if user can edit rides (only Sriharsha, Venkatesh, Mihir)
            if current_rider:
                allowed_names = ['sriharsha', 'venkatesh', 'mihir']
                can_edit_rides = current_rider.get('first_name', '').lower() in allowed_names
            
            # Batch load signup statuses for all events (1 query instead of N queries)
            user_signup_statuses = get_rider_signup_statuses_batch(rider_id, ride_ids)
            user_signups = {ride_id: data['status'] for ride_id, data in user_signup_statuses.items()}
            
            # Load custom plans for this rider
            # First, build a map of plan_slug -> plan_id
            plan_slug_to_id = {plan['slug']: plan['id'] for plan in plans}
            
            for event in rusa_events:
                if event.get('plan_slug'):
                    plan_id = plan_slug_to_id.get(event['plan_slug'])
                    if plan_id:
                        custom_plan = get_custom_plan(rider_id, plan_id)
                        if custom_plan:
                            user_custom_plans[event['plan_slug']] = custom_plan

    # Add signup counts and custom plan info to events
    for event in rusa_events:
        event['signup_count'] = signup_counts.get(event['id'], 0)
        if event.get('plan_slug'):
            event['has_custom_plan'] = event['plan_slug'] in user_custom_plans

    # Region color map
    region_colors = {
        'San Francisco': '#e74c3c',
        'Davis': '#2ecc71',
        'Santa Cruz': '#3498db',
    }

    # Build distance filter from actual event data
    distances = sorted(set(e['distance_km'] for e in rusa_events if e.get('distance_km')))

    label = SEASON_LABELS.get(season_name, f'{season_name} Season')

    # Get all ride plans for the edit modal
    all_ride_plans = get_all_ride_plans()
    
    return render_template('upcoming_brevets.html',
                           season=season,
                           season_label=label,
                           rusa_events=rusa_events,
                           future_rides=future_rides,
                           is_current=is_current,
                           region_colors=region_colors,
                           distances=distances,
                           current_rider_id=rider_id,
                           user_signups=user_signups,
                           all_ride_plans=all_ride_plans,
                           can_edit_rides=can_edit_rides)


@riders_bp.route('/ride/<int:ride_id>/edit', methods=['GET', 'POST'])
@user_login_required
def edit_ride(ride_id):
    """Edit ride details (route, team route, start time, location, time limit)."""
    from flask import jsonify, session
    
    # Check permissions - only Sriharsha, Venkatesh, Mihir can edit
    user_id = session.get('user_id')
    if user_id:
        user = get_user_by_id(user_id)
        if user and user.get('rider_id'):
            current_rider = _execute("SELECT * FROM rider WHERE id = %s", (user['rider_id'],)).fetchone()
            if current_rider:
                allowed_names = ['sriharsha', 'venkatesh', 'mihir']
                if current_rider.get('first_name', '').lower() not in allowed_names:
                    abort(403)
            else:
                abort(403)
        else:
            abort(403)
    else:
        abort(403)
    
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)
    
    if request.method == 'POST':
        # Get form data
        rwgps_url = request.form.get('rwgps_url', '').strip()
        ride_plan_id = request.form.get('ride_plan_id')
        start_time = request.form.get('start_time', '').strip()
        start_location = request.form.get('start_location', '').strip()
        time_limit_hours = request.form.get('time_limit_hours')
        
        # Convert empty strings to None
        ride_plan_id = int(ride_plan_id) if ride_plan_id and ride_plan_id != '' else None
        time_limit_hours = float(time_limit_hours) if time_limit_hours and time_limit_hours != '' else None
        
        # Update the ride
        update_ride_details(
            ride_id=ride_id,
            rwgps_url=rwgps_url if rwgps_url else None,
            ride_plan_id=ride_plan_id,
            start_time=start_time if start_time else None,
            start_location=start_location if start_location else None,
            time_limit_hours=time_limit_hours
        )
        cache.clear()  # Clear cache after ride update
        
        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True})
        
        # Redirect back to upcoming brevets for regular form submission
        current_season = get_current_season()
        if current_season:
            return redirect(url_for('riders.upcoming_brevets', season_name=current_season['name']))
        return redirect(url_for('main.index'))
    
    # GET request - show edit form
    ride_plans = get_all_ride_plans()
    return render_template('edit_ride.html', ride=ride, ride_plans=ride_plans)


@riders_bp.route('/rider/<int:rusa_id>')
def rider_profile(rusa_id):
    from flask import session
    
    rider = get_rider_by_rusa(rusa_id)
    if not rider:
        abort(404)

    # Check if logged-in user is viewing their own profile
    is_own_profile = session.get('rider_id') == rider['id']
    
    # Determine if Strava data should be visible
    strava_data_private = rider.get('strava_data_private', False)
    show_strava_data = is_own_profile or not strava_data_private

    seasons = get_all_seasons()
    current = get_current_season()

    season_data = []
    career_rides = 0
    career_kms = 0

    for s in seasons:
        participation = get_rider_participation(rider['id'], s['id'])
        stats = get_rider_season_stats(rider['id'], s['id'])
        is_cur = current and current['id'] == s['id']
        sr_n = detect_sr_for_rider_season(rider['id'], s['id'], date_filter=is_cur)

        if participation:
            season_data.append({
                'season': s,
                'participation': participation,
                'rides': stats['rides'],
                'kms': stats['kms'],
                'sr_count': sr_n,
                'is_current': is_cur,
            })
            career_rides += stats['rides']
            career_kms += stats['kms']

    total_srs = get_rider_total_srs(rider['id'])

    # --- R-12 awards ---
    r12_awards = detect_r12_awards(rider['id'])
    total_r12s = len(r12_awards)
    # Build set of end_years for showing R-12 in season blocks
    r12_years = set(a['end_year'] for a in r12_awards)

    # --- Strava training data ---
    strava_connection = get_strava_connection(rider['id'])
    training_rides = []
    fitness_score = None
    has_strava = False
    activities = []

    # Only load Strava data if it should be visible
    if strava_connection and show_strava_data:
        has_strava = True
        activities = get_strava_activities(rider['id'], days=28)
        if activities:
            fitness_score = calculate_fitness_score(activities)
            training_rides = score_all_activities(activities)

    # --- Upcoming rides with readiness ---
    upcoming_rides = []
    signups = get_rider_upcoming_signups(rider['id'])
    
    # Convert signups to list of dicts and match ride plans (same logic as upcoming_brevets)
    signups_list = []
    for s in signups:
        ride_dict = dict(s)
        ride_dict['route_name'] = ride_dict.get('name', '')  # Add route_name for matching
        signups_list.append(ride_dict)
    
    plans = get_all_ride_plans()
    _match_plans_to_events(signups_list, plans)

    # Pass 1: compute readiness for all rides, collect context for AI
    rides_for_ai = []
    today = date.today()
    for ride_dict in signups_list:
        # Calculate days until ride and check if within 7 days
        ride_date = ride_dict.get('date')
        if ride_date:
            if isinstance(ride_date, str):
                ride_date = datetime.strptime(ride_date, '%Y-%m-%d').date()
            days_until = (ride_date - today).days
            ride_dict['days_until'] = days_until
            ride_dict['is_soon'] = 0 <= days_until <= 7
        else:
            ride_dict['days_until'] = 999
            ride_dict['is_soon'] = False
        
        if has_strava and activities:
            readiness = assess_readiness(activities, ride_dict)
            ride_date = ride_dict.get('date')
            if ride_date:
                if isinstance(ride_date, str):
                    ride_date = datetime.strptime(ride_date, '%Y-%m-%d').date()
                weeks_until = max(0, (ride_date - today).days // 7)
            else:
                weeks_until = 4
            ride_dict['readiness'] = readiness
            ride_dict['_weeks_until'] = weeks_until
        else:
            ride_dict['readiness'] = None
            ride_dict['_weeks_until'] = 4
        rides_for_ai.append({
            'ride': ride_dict,
            'readiness': ride_dict.get('readiness'),
            'weeks_until': ride_dict.get('_weeks_until', 4),
            'signup_status': ride_dict.get('signup_status', 'GOING'),
        })
        upcoming_rides.append(ride_dict)

    # Assign rule-based advice immediately (AI advice loaded async after page load)
    for ride_dict in upcoming_rides:
        if ride_dict.get('readiness') is not None:
            weeks_until = ride_dict.pop('_weeks_until', 4)
            ride_dict['advice'] = generate_training_advice(
                ride_dict['readiness'], ride_dict, weeks_until
            )
        else:
            # No Strava — show placeholder so advice button appears;
            # AI advice will load async using brevet history as signal
            ride_dict['advice'] = ['Loading AI coaching advice based on your brevet history...']
        ride_dict.pop('_weeks_until', None)

    return render_template('rider_profile.html',
                           rider=rider,
                           season_data=season_data,
                           career_rides=career_rides,
                           career_kms=career_kms,
                           total_srs=total_srs,
                           has_strava=has_strava,
                           training_rides=training_rides,
                           fitness_score=fitness_score,
                           upcoming_rides=upcoming_rides,
                           total_r12s=total_r12s,
                           r12_awards=r12_awards,
                           r12_years=r12_years,
                           is_own_profile=is_own_profile,
                           show_strava_data=show_strava_data)


@riders_bp.route('/rider/<int:rusa_id>/advice')
def rider_advice_api(rusa_id):
    """Async API endpoint: returns AI coaching advice as JSON."""
    rider = get_rider_by_rusa(rusa_id)
    if not rider:
        return jsonify({}), 404

    # Determine if Strava data should be visible
    is_own_profile = session.get('rider_id') == rider['id']
    strava_data_private = rider.get('strava_data_private', False)
    show_strava_data = is_own_profile or not strava_data_private

    # Load Strava data
    strava_connection = get_strava_connection(rider['id'])
    activities = []
    fitness_score = None
    if strava_connection and show_strava_data:
        activities = get_strava_activities(rider['id'], days=28)
        if activities:
            fitness_score = calculate_fitness_score(activities)

    # Build season data for brevet history fallback
    seasons = get_all_seasons()
    current = get_current_season()
    season_data = []
    for s in seasons:
        participation = get_rider_participation(rider['id'], s['id'])
        stats = get_rider_season_stats(rider['id'], s['id'])
        is_cur = current and current['id'] == s['id']
        if participation:
            season_data.append({
                'season': s,
                'participation': participation,
                'rides': stats['rides'],
                'kms': stats['kms'],
                'is_current': is_cur,
            })

    # Build upcoming rides with readiness
    signups = get_rider_upcoming_signups(rider['id'])
    signups_list = []
    for s in signups:
        ride_dict = dict(s)
        ride_dict['route_name'] = ride_dict.get('name', '')
        signups_list.append(ride_dict)

    plans = get_all_ride_plans()
    _match_plans_to_events(signups_list, plans)

    rides_for_ai = []
    today = date.today()
    for ride_dict in signups_list:
        ride_date = ride_dict.get('date')
        if ride_date:
            if isinstance(ride_date, str):
                ride_date = datetime.strptime(ride_date, '%Y-%m-%d').date()
            weeks_until = max(0, (ride_date - today).days // 7)
        else:
            weeks_until = 4

        if activities:
            readiness = assess_readiness(activities, ride_dict)
            ride_dict['readiness'] = readiness
        else:
            ride_dict['readiness'] = None

        rides_for_ai.append({
            'ride': ride_dict,
            'readiness': ride_dict.get('readiness'),
            'weeks_until': weeks_until,
            'signup_status': ride_dict.get('signup_status', 'GOING'),
        })

    ai_advice = {}
    if rides_for_ai:
        ai_advice = generate_openai_advice(
            rider, activities, fitness_score, rides_for_ai, season_data
        )

    # Return as {ride_id_str: advice_string}
    return jsonify({str(k): v for k, v in ai_advice.items()})


@riders_bp.route('/rider/<int:rusa_id>/edit', methods=['GET', 'POST'])
@login_required
def rider_edit(rusa_id):
    rider = get_rider_by_rusa(rusa_id)
    if not rider:
        abort(404)

    if request.method == 'POST':
        bio = request.form.get('bio', '')
        photo = request.files.get('photo')
        photo_filename = None
        if photo and photo.filename:
            from werkzeug.utils import secure_filename
            import os
            from flask import current_app
            photo_filename = secure_filename(f"{rider['first_name'].lower()}_{rider['last_name'].lower()}.jpg")
            photo.save(os.path.join(current_app.config['UPLOAD_FOLDER'], photo_filename))

        update_rider_profile(rider['id'], photo_filename=photo_filename, bio=bio)
        cache.clear()  # Clear cache after profile update
        return redirect(url_for('riders.rider_profile', rusa_id=rusa_id))

    return render_template('rider_edit.html', rider=rider)


@riders_bp.route('/rider/<int:rusa_id>/toggle-strava-privacy', methods=['POST'])
def toggle_strava_privacy(rusa_id):
    from flask import jsonify, session
    
    rider = get_rider_by_rusa(rusa_id)
    if not rider:
        abort(404)
    
    # Only allow the rider to toggle their own privacy
    if session.get('rider_id') != rider['id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        is_private = request.json.get('is_private', False)
        update_strava_privacy(rider['id'], is_private)
        cache.clear()  # Clear cache after privacy update
        return jsonify({'success': True, 'is_private': is_private})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/ride-plans')
@cache.cached(timeout=CACHE_TIMEOUT, key_prefix='ride_plans_index')
def ride_plans_index():
    plans = get_all_ride_plans()
    return render_template('ride_plans.html', plans=plans)


@riders_bp.route('/ride-plan/<slug>')
def ride_plan_detail(slug):
    # Check if user wants to view their custom plan
    view = request.args.get('view', 'base')
    
    if view == 'custom':
        # Redirect to custom plan view function
        return custom_ride_plan_view(slug)
    
    # Otherwise show base plan
    plan = get_ride_plan_by_slug(slug)
    if not plan:
        abort(404)
    raw_stops = get_ride_plan_stops(plan['id'])

    # Convert Decimal types to float for Jinja2 arithmetic
    plan = dict(plan)
    plan['total_distance_miles'] = float(plan.get('total_distance_miles') or 0)
    plan['total_elevation_ft'] = int(plan.get('total_elevation_ft') or 0)

    # Extract distance class for bookend time calculation
    distance_km = _extract_distance_km(plan['name'])
    cutoff_hours = _get_cutoff_hours(distance_km)
    plan['distance_km'] = distance_km
    plan['cutoff_hours'] = cutoff_hours
    plan['start_time'] = plan.get('start_time') or '06:00'

    # Determine which RWGPS link to show (team preferred, else official)
    rwgps_url_display = plan.get('rwgps_url_team') or plan.get('rwgps_url')
    rwgps_url_label = 'Team Asha Route' if plan.get('rwgps_url_team') else 'Official Route'
    rwgps_route_id = _extract_rwgps_route_id(rwgps_url_display)
    
    # For weather forecast, always prefer Team Asha route if available
    weather_route_id = _extract_rwgps_route_id(plan.get('rwgps_url_team')) if plan.get('rwgps_url_team') else rwgps_route_id

    stops = []
    cum_time_min = 0
    prev_dist = 0.0
    total_moving_time = 0
    total_break_time = 0

    for s in raw_stops:
        d = dict(s)
        d['distance_miles'] = float(d['distance_miles']) if d.get('distance_miles') is not None else None
        d['elevation_gain'] = int(d['elevation_gain']) if d.get('elevation_gain') is not None else None
        d['segment_time_min'] = int(d['segment_time_min']) if d.get('segment_time_min') is not None else None
        d['stop_duration_min'] = int(d['stop_duration_min']) if d.get('stop_duration_min') is not None else 0

        cur_dist = d['distance_miles'] or 0.0
        seg_dist = round(cur_dist - prev_dist, 1)
        d['seg_dist'] = seg_dist

        # Ft/mile for this segment
        d['ft_per_mi'] = int(round(d['elevation_gain'] / seg_dist)) if d.get('elevation_gain') and seg_dist > 0 else None

        # Average speed for this segment (based on segment time only, not including stop duration)
        d['avg_speed'] = round(seg_dist / (d['segment_time_min'] / 60.0), 1) if d.get('segment_time_min') and d['segment_time_min'] > 0 and seg_dist > 0 else None

        # Cumulative time includes both segment time (riding) and stop duration (rest)
        if d['segment_time_min']:
            cum_time_min += d['segment_time_min']
            total_moving_time += d['segment_time_min']
        
        # Add stop duration to cumulative time
        if d['stop_duration_min']:
            cum_time_min += d['stop_duration_min']
            total_break_time += d['stop_duration_min']
        
        d['cum_time_min'] = cum_time_min
        
        # Arrival time: cumulative time minus stop duration (time you arrive, before resting)
        d['arrival_time_min'] = cum_time_min - (d['stop_duration_min'] or 0)

        # Bookend time: max allowed time to reach this point (arrival, not departure)
        if cutoff_hours and plan['total_distance_miles'] > 0 and d['distance_miles']:
            fraction = d['distance_miles'] / plan['total_distance_miles']
            d['bookend_time_min'] = round(fraction * cutoff_hours * 60)
            # Time bank should be based on arrival time, not departure time
            d['time_bank_min'] = d['bookend_time_min'] - d['arrival_time_min']
        else:
            d['bookend_time_min'] = None
            d['time_bank_min'] = None

        # Difficulty scoring
        d['difficulty_score'] = _compute_difficulty_score(d['ft_per_mi'], d.get('notes'))
        d['difficulty_label'] = _difficulty_label(d['difficulty_score'])
        d['difficulty_color'] = _difficulty_color(d['ft_per_mi'])

        # Terrain difficulty label (kept for compatibility)
        if d['ft_per_mi']:
            if d['ft_per_mi'] >= 80:
                d['terrain_label'] = 'steep'
            elif d['ft_per_mi'] >= 50:
                d['terrain_label'] = 'rolling'
            elif d['ft_per_mi'] >= 25:
                d['terrain_label'] = 'moderate'
            else:
                d['terrain_label'] = 'flat'
        else:
            d['terrain_label'] = None

        prev_dist = cur_dist
        stops.append(d)

    total_time = cum_time_min

    # Plan-level aggregates
    avg_moving_speed = round(plan['total_distance_miles'] / (total_moving_time / 60.0), 1) if total_moving_time > 0 else None
    avg_elapsed_speed = round(plan['total_distance_miles'] / (total_time / 60.0), 1) if total_time > 0 else None
    overall_ft_per_mile = round(plan['total_elevation_ft'] / plan['total_distance_miles'], 0) if plan['total_distance_miles'] > 0 else 0
    
    # Calculate weighted difficulty (distance-weighted average of difficulty scores)
    weighted_difficulty = None
    total_moving_distance = 0
    weighted_difficulty_sum = 0
    for s in stops:
        if s.get('seg_dist') and s['seg_dist'] > 0 and s.get('difficulty_score'):
            total_moving_distance += s['seg_dist']
            weighted_difficulty_sum += s['difficulty_score'] * s['seg_dist']
    if total_moving_distance > 0:
        weighted_difficulty = round(weighted_difficulty_sum / total_moving_distance, 1)

    # Build collapsed journey nodes
    journey_nodes = _build_journey_nodes(stops)

    # Check if there's an upcoming RUSA event that matches this ride plan
    upcoming_event = None
    signup_count = 0
    user_signup_status = None
    from datetime import datetime, timedelta, date as date_type
    from models import get_upcoming_rusa_events, get_user_by_id
    from flask import session
    
    rusa_events = get_upcoming_rusa_events()
    today = date_type.today()
    thirty_days_later = today + timedelta(days=30)
    
    for event in rusa_events:
        e_words = _normalize_route(event.get('route_name', ''))
        p_words = _normalize_route(plan['name'])
        common = e_words & p_words
        distinctive = common - _GENERIC_WORDS
        if len(distinctive) >= 1 and len(common) >= 2:
            # Check if event is within 30 days
            event_date = event['date']
            # Convert to date object if it's a string
            if isinstance(event_date, str):
                event_date = datetime.strptime(event_date, '%Y-%m-%d').date()
            
            if event_date >= today and event_date <= thirty_days_later:
                upcoming_event = event
                signup_count = get_signup_count(event['id'])
                
                # Check current user's signup status
                user_id = session.get('user_id')
                if user_id:
                    user = get_user_by_id(user_id)
                    if user and user.get('rider_id'):
                        status = get_rider_signup_status(user['rider_id'], event['id'])
                        if status:
                            user_signup_status = status['status']
                break
    
    # Check if user has custom plan for this base plan
    user_custom_plan = None
    public_custom_plans = []
    user_id = session.get('user_id')
    if user_id:
        user = get_user_by_id(user_id)
        if user and user.get('rider_id'):
            user_custom_plan = get_custom_plan(user['rider_id'], plan['id'])
    
    # Get public custom plans from other riders
    public_custom_plans = get_public_custom_plans(plan['id'])

    # Attach break merging metadata for timeline layout
    stops, use_timeline = _attach_break_metadata(stops)

    return render_template('ride_plan_detail.html',
                           plan=plan,
                           stops=stops,
                           use_timeline=use_timeline,
                           total_time=total_time,
                           total_moving_time=total_moving_time,
                           total_break_time=total_break_time,
                           avg_moving_speed=avg_moving_speed,
                           avg_elapsed_speed=avg_elapsed_speed,
                           overall_ft_per_mile=overall_ft_per_mile,
                           weighted_difficulty=weighted_difficulty,
                           journey_nodes=journey_nodes,
                           rwgps_url_display=rwgps_url_display,
                           rwgps_url_label=rwgps_url_label,
                           rwgps_route_id=rwgps_route_id,
                           weather_route_id=weather_route_id,
                           difficulty_colors=_DIFFICULTY_COLORS,
                           upcoming_event=upcoming_event,
                           signup_count=signup_count,
                           user_signup_status=user_signup_status,
                           user_custom_plan=user_custom_plan,
                           public_custom_plans=public_custom_plans,
                           is_custom_view=False,
                           is_admin=is_admin_user())


# ========== CUSTOM RIDE PLANS ==========

@riders_bp.route('/ride-plan/<slug>/edit-base')
@user_login_required
def base_plan_editor(slug):
    """Admin-only editor for base ride plans."""
    if not is_admin_user():
        abort(403)
    
    # Get base plan
    base_plan = get_ride_plan_by_slug(slug)
    if not base_plan:
        abort(404)
    
    # Load base stops
    base_stops = list(get_ride_plan_stops(base_plan['id']))
    from services.custom_plan_service import recalculate_cumulative_values
    base_stops = recalculate_cumulative_values(base_stops, base_plan)
    
    # Convert Decimal types
    base_plan = dict(base_plan)
    base_plan['total_distance_miles'] = float(base_plan.get('total_distance_miles') or 0)
    base_plan['total_elevation_ft'] = int(base_plan.get('total_elevation_ft') or 0)
    
    return render_template('base_ride_plan_editor.html',
                           base_plan=base_plan,
                           base_stops=base_stops)

def custom_ride_plan_view(slug):
    """View custom plan with same detail as base plan, but with custom timings."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return redirect(url_for('riders.ride_plan_detail', slug=slug))
    
    rider_id = user['rider_id']
    
    # Get base plan
    base_plan = get_ride_plan_by_slug(slug)
    if not base_plan:
        abort(404)
    
    # Get custom plan
    custom_plan = get_custom_plan(rider_id, base_plan['id'])
    if not custom_plan:
        # No custom plan, redirect to base plan
        return redirect(url_for('riders.ride_plan_detail', slug=slug))
    
    # Get custom stops (merged with overrides)
    from services.custom_plan_service import get_merged_plan_stops, recalculate_cumulative_values
    custom_stops_raw, custom_plan_data = get_merged_plan_stops(custom_plan['id'])
    
    # Convert Decimal types to float for Jinja2 arithmetic
    plan = dict(base_plan)
    plan['total_distance_miles'] = float(base_plan.get('total_distance_miles') or 0)
    plan['total_elevation_ft'] = int(base_plan.get('total_elevation_ft') or 0)
    
    # Override name with custom plan name for display
    plan['custom_name'] = custom_plan_data.get('name') or f"My {base_plan['name']}"
    
    # Extract distance class for bookend time calculation
    distance_km = _extract_distance_km(plan['name'])
    cutoff_hours = _get_cutoff_hours(distance_km)
    plan['distance_km'] = distance_km
    plan['cutoff_hours'] = cutoff_hours
    plan['start_time'] = plan.get('start_time') or '06:00'
    
    # Determine which RWGPS link to show
    rwgps_url_display = plan.get('rwgps_url_team') or plan.get('rwgps_url')
    rwgps_url_label = 'Team Asha Route' if plan.get('rwgps_url_team') else 'Official Route'
    rwgps_route_id = _extract_rwgps_route_id(rwgps_url_display)
    weather_route_id = _extract_rwgps_route_id(plan.get('rwgps_url_team')) if plan.get('rwgps_url_team') else rwgps_route_id
    
    # Process stops with full detail (same as base plan view)
    stops = []
    cum_time_min = 0
    prev_dist = 0.0
    total_moving_time = 0
    total_break_time = 0
    
    for s in custom_stops_raw:
        d = dict(s)
        d['distance_miles'] = float(d['distance_miles']) if d.get('distance_miles') is not None else None
        d['elevation_gain'] = int(d['elevation_gain']) if d.get('elevation_gain') is not None else None
        d['segment_time_min'] = int(d['segment_time_min']) if d.get('segment_time_min') is not None else None
        d['stop_duration_min'] = int(d['stop_duration_min']) if d.get('stop_duration_min') is not None else 0
        
        cur_dist = d['distance_miles'] or 0.0
        seg_dist = round(cur_dist - prev_dist, 1)
        d['seg_dist'] = seg_dist
        
        # Ft/mile for this segment
        d['ft_per_mi'] = int(round(d['elevation_gain'] / seg_dist)) if d.get('elevation_gain') and seg_dist > 0 else None
        
        # Average speed for this segment (based on segment time only, not including stop duration)
        d['avg_speed'] = round(seg_dist / (d['segment_time_min'] / 60.0), 1) if d.get('segment_time_min') and d['segment_time_min'] > 0 and seg_dist > 0 else None
        
        # Cumulative time includes both segment time (riding) and stop duration (rest)
        if d['segment_time_min']:
            cum_time_min += d['segment_time_min']
            total_moving_time += d['segment_time_min']
        
        # Add stop duration to cumulative time
        if d['stop_duration_min']:
            cum_time_min += d['stop_duration_min']
            total_break_time += d['stop_duration_min']
        
        d['cum_time_min'] = cum_time_min
        
        # Arrival time: cumulative time minus stop duration (time you arrive, before resting)
        d['arrival_time_min'] = cum_time_min - (d['stop_duration_min'] or 0)
        
        # Bookend time: max allowed time to reach this point (arrival, not departure)
        if cutoff_hours and plan['total_distance_miles'] > 0 and d['distance_miles']:
            fraction = d['distance_miles'] / plan['total_distance_miles']
            d['bookend_time_min'] = round(fraction * cutoff_hours * 60)
            # Time bank should be based on arrival time, not departure time
            d['time_bank_min'] = d['bookend_time_min'] - d['arrival_time_min']
        else:
            d['bookend_time_min'] = None
            d['time_bank_min'] = None
        
        # Difficulty scoring
        d['difficulty_score'] = _compute_difficulty_score(d['ft_per_mi'], d.get('notes'))
        d['difficulty_label'] = _difficulty_label(d['difficulty_score'])
        d['difficulty_color'] = _difficulty_color(d['ft_per_mi'])
        
        # Terrain difficulty label
        if d['ft_per_mi']:
            if d['ft_per_mi'] >= 80:
                d['terrain_label'] = 'steep'
            elif d['ft_per_mi'] >= 50:
                d['terrain_label'] = 'rolling'
            elif d['ft_per_mi'] >= 25:
                d['terrain_label'] = 'moderate'
            else:
                d['terrain_label'] = 'flat'
        else:
            d['terrain_label'] = None
        
        prev_dist = cur_dist
        stops.append(d)
    
    total_time = cum_time_min
    
    # Plan-level aggregates
    avg_moving_speed = round(plan['total_distance_miles'] / (total_moving_time / 60.0), 1) if total_moving_time > 0 else None
    avg_elapsed_speed = round(plan['total_distance_miles'] / (total_time / 60.0), 1) if total_time > 0 else None
    overall_ft_per_mile = round(plan['total_elevation_ft'] / plan['total_distance_miles'], 0) if plan['total_distance_miles'] > 0 else 0
    
    # Calculate weighted difficulty (distance-weighted average of difficulty scores)
    weighted_difficulty = None
    total_moving_distance = 0
    weighted_difficulty_sum = 0
    for s in stops:
        if s.get('seg_dist') and s['seg_dist'] > 0 and s.get('difficulty_score'):
            total_moving_distance += s['seg_dist']
            weighted_difficulty_sum += s['difficulty_score'] * s['seg_dist']
    if total_moving_distance > 0:
        weighted_difficulty = round(weighted_difficulty_sum / total_moving_distance, 1)
    
    # Build collapsed journey nodes
    journey_nodes = _build_journey_nodes(stops)

    # Attach break merging metadata for timeline layout
    stops, use_timeline = _attach_break_metadata(stops)

    # Check if there's an upcoming RUSA event that matches this ride plan
    upcoming_event = None
    signup_count = 0
    user_signup_status = None
    from datetime import datetime, timedelta, date as date_type
    
    rusa_events = get_upcoming_rusa_events()
    today = date_type.today()
    thirty_days_later = today + timedelta(days=30)
    
    for event in rusa_events:
        e_words = _normalize_route(event.get('route_name', ''))
        p_words = _normalize_route(plan['name'])
        common = e_words & p_words
        distinctive = common - _GENERIC_WORDS
        if len(distinctive) >= 1 and len(common) >= 2:
            # Check if event is within 30 days
            event_date = event['date']
            # Convert to date object if it's a string
            if isinstance(event_date, str):
                event_date = datetime.strptime(event_date, '%Y-%m-%d').date()
            
            if event_date >= today and event_date <= thirty_days_later:
                upcoming_event = event
                signup_count = get_signup_count(event['id'])
                
                # Check current user's signup status
                if user and user.get('rider_id'):
                    status = get_rider_signup_status(user['rider_id'], event['id'])
                    if status:
                        user_signup_status = status['status']
                break
    
    return render_template('ride_plan_detail.html',
                         plan=plan,
                         stops=stops,
                         use_timeline=use_timeline,
                         journey_nodes=journey_nodes,
                         total_time=total_time,
                         total_moving_time=total_moving_time,
                         total_break_time=total_break_time,
                         avg_moving_speed=avg_moving_speed,
                         avg_elapsed_speed=avg_elapsed_speed,
                         overall_ft_per_mile=overall_ft_per_mile,
                         weighted_difficulty=weighted_difficulty,
                         rwgps_url_display=rwgps_url_display,
                         rwgps_url_label=rwgps_url_label,
                         rwgps_route_id=rwgps_route_id,
                         weather_route_id=weather_route_id,
                         upcoming_event=upcoming_event,
                         signup_count=signup_count,
                         user_signup_status=user_signup_status,
                         user_custom_plan=custom_plan_data,
                         public_custom_plans=[],
                         is_custom_view=True,
                         is_admin=is_admin_user())


@riders_bp.route('/ride-plan/<slug>/custom')
@user_login_required
def custom_ride_plan_editor(slug):
    """View/edit custom ride plan page."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return redirect(url_for('auth.complete_profile'))
    
    rider_id = user['rider_id']
    
    # Get base plan
    base_plan = get_ride_plan_by_slug(slug)
    if not base_plan:
        abort(404)
    
    # Check if user has custom plan
    custom_plan = get_custom_plan(rider_id, base_plan['id'])
    
    if custom_plan:
        # Convert to dict and ensure avg_moving_speed is float
        custom_plan = dict(custom_plan)
        if custom_plan.get('avg_moving_speed') is not None:
            custom_plan['avg_moving_speed'] = float(custom_plan['avg_moving_speed'])
        
        # Load base stops and calculate all metrics for reference/comparison
        base_stops_raw = get_ride_plan_stops(base_plan['id'])
        
        # Calculate derived metrics for base stops
        distance_km = _extract_distance_km(base_plan['name'])
        cutoff_hours = _get_cutoff_hours(distance_km)
        total_distance = float(base_plan.get('total_distance_miles') or 0)
        
        base_stops = []
        prev_dist = 0.0
        cum_time_min = 0
        
        for s in base_stops_raw:
            stop = dict(s)
            stop['distance_miles'] = float(stop['distance_miles']) if stop.get('distance_miles') is not None else None
            stop['elevation_gain'] = int(stop['elevation_gain']) if stop.get('elevation_gain') is not None else None
            stop['segment_time_min'] = int(stop['segment_time_min']) if stop.get('segment_time_min') is not None else None
            
            cur_dist = stop['distance_miles'] or 0.0
            seg_dist = round(cur_dist - prev_dist, 1)
            stop['seg_dist'] = seg_dist
            
            # Ft/mile
            elev = stop.get('elevation_gain') or 0
            stop['ft_per_mi'] = int(round(elev / seg_dist)) if seg_dist > 0 and elev > 0 else None
            
            # Avg speed
            seg_time = stop.get('segment_time_min') or 0
            stop['avg_speed'] = round(seg_dist / (seg_time / 60.0), 1) if seg_time > 0 and seg_dist > 0 else None
            
            # Cumulative time
            if seg_time:
                cum_time_min += seg_time
            stop['cum_time_min'] = cum_time_min
            
            # Time bank
            if cutoff_hours and total_distance > 0 and cur_dist > 0:
                fraction = cur_dist / total_distance
                bookend_time_min = round(fraction * cutoff_hours * 60)
                stop['time_bank_min'] = bookend_time_min - cum_time_min
            else:
                stop['time_bank_min'] = None
            
            # Difficulty score
            stop['difficulty_score'] = _compute_difficulty_score(stop['ft_per_mi'], stop.get('notes'))
            stop['difficulty_label'] = _difficulty_label(stop['difficulty_score'])
            
            prev_dist = cur_dist
            base_stops.append(stop)
        
        custom_stops_raw = get_custom_plan_stops_raw(custom_plan['id'])
        
        # Build maps for efficient lookup
        base_stops_map = {s['id']: dict(s) for s in base_stops}
        custom_overrides = {}
        custom_only_stops = []
        
        for cs in custom_stops_raw:
            cs_dict = dict(cs)
            if cs_dict.get('base_stop_id'):
                # This is an override of a base stop
                custom_overrides[cs_dict['stop_order']] = cs_dict
            elif cs_dict.get('is_custom_stop'):
                # This is a custom-added stop
                custom_only_stops.append(cs_dict)
        
        # Build the final merged list sorted by distance
        custom_stops = []
        
        # Add all non-deleted base stops
        hidden_base_stop_ids = set()
        for cs in custom_stops_raw:
            if cs.get('base_stop_id') and cs.get('is_hidden'):
                hidden_base_stop_ids.add(cs['base_stop_id'])
        
        for bs in base_stops:
            if bs['id'] not in hidden_base_stop_ids:
                stop = dict(bs)
                stop['is_modified'] = False
                stop['is_custom_stop'] = False
                stop['custom_stop_id'] = None
                
                # Check for overrides
                override = next((cs for cs in custom_stops_raw 
                               if cs.get('base_stop_id') == bs['id'] and not cs.get('is_hidden')), None)
                if override:
                    if override.get('segment_time_min') is not None:
                        stop['segment_time_min'] = int(override['segment_time_min'])
                        stop['is_modified'] = True
                    if override.get('distance_miles') is not None:
                        stop['distance_miles'] = float(override['distance_miles'])
                        stop['is_modified'] = True
                    if override.get('elevation_gain') is not None:
                        stop['elevation_gain'] = int(override['elevation_gain'])
                        stop['is_modified'] = True
                    if override.get('notes'):
                        stop['notes'] = override['notes']
                        stop['is_modified'] = True
                    
                    # Handle stop_duration_min and stop_name with inheritance logic
                    override_duration = override.get('stop_duration_min')
                    if override_duration == -1:
                        # Explicitly removed - clear both
                        stop['stop_duration_min'] = 0
                        stop['stop_name'] = None
                        stop['is_modified'] = True
                    elif override_duration is not None and override_duration > 0:
                        # Custom duration > 0: use custom duration
                        stop['stop_duration_min'] = int(override_duration)
                        # Use custom name if present (not null), otherwise keep base
                        if override.get('stop_name') is not None:
                            stop['stop_name'] = override['stop_name']
                        stop['is_modified'] = True
                    # else: duration is NULL or 0 - inherit both from base (already in stop)
                    
                    stop['custom_stop_id'] = override['id']
                
                # Convert types
                if stop.get('distance_miles') is not None:
                    stop['distance_miles'] = float(stop['distance_miles'])
                if stop.get('elevation_gain') is not None:
                    stop['elevation_gain'] = int(stop['elevation_gain'])
                if stop.get('segment_time_min') is not None:
                    stop['segment_time_min'] = int(stop['segment_time_min'])
                
                custom_stops.append(stop)
        
        # Add custom-only stops
        for cs in custom_stops_raw:
            if cs.get('is_custom_stop'):
                stop = dict(cs)
                stop['is_custom_stop'] = True
                stop['is_modified'] = True
                stop['custom_stop_id'] = cs['id']
                
                # Convert types
                if stop.get('distance_miles') is not None:
                    stop['distance_miles'] = float(stop['distance_miles'])
                if stop.get('elevation_gain') is not None:
                    stop['elevation_gain'] = int(stop['elevation_gain'])
                if stop.get('segment_time_min') is not None:
                    stop['segment_time_min'] = int(stop['segment_time_min'])
                
                custom_stops.append(stop)
        
        # Sort by distance_miles for proper display order
        custom_stops.sort(key=lambda s: (s.get('distance_miles') or 0, s.get('stop_order', 999)))
        
        # Calculate derived metrics for editor display
        distance_km = _extract_distance_km(base_plan['name'])
        cutoff_hours = _get_cutoff_hours(distance_km)
        total_distance = float(base_plan.get('total_distance_miles') or 0)
        
        prev_dist = 0.0
        cum_time_min = 0
        
        for stop in custom_stops:
            cur_dist = stop.get('distance_miles') or 0.0
            seg_dist = round(cur_dist - prev_dist, 1)
            stop['seg_dist'] = seg_dist
            
            # Ft/mile
            elev = stop.get('elevation_gain') or 0
            stop['ft_per_mi'] = int(round(elev / seg_dist)) if seg_dist > 0 and elev > 0 else None
            
            # Avg speed
            seg_time = stop.get('segment_time_min') or 0
            stop['avg_speed'] = round(seg_dist / (seg_time / 60.0), 1) if seg_time > 0 and seg_dist > 0 else None
            
            # Cumulative time
            if seg_time:
                cum_time_min += seg_time
            stop['cum_time_min'] = cum_time_min
            
            # Time bank
            if cutoff_hours and total_distance > 0 and cur_dist > 0:
                fraction = cur_dist / total_distance
                bookend_time_min = round(fraction * cutoff_hours * 60)
                stop['time_bank_min'] = bookend_time_min - cum_time_min
            else:
                stop['time_bank_min'] = None
            
            # Difficulty score
            stop['difficulty_score'] = _compute_difficulty_score(stop['ft_per_mi'], stop.get('notes'))
            stop['difficulty_label'] = _difficulty_label(stop['difficulty_score'])
            
            prev_dist = cur_dist
    else:
        # No custom plan yet - show base plan only
        custom_plan = None
        custom_stops = None
        base_stops_raw = get_ride_plan_stops(base_plan['id'])
        
        # Calculate derived metrics for base stops
        distance_km = _extract_distance_km(base_plan['name'])
        cutoff_hours = _get_cutoff_hours(distance_km)
        total_distance = float(base_plan.get('total_distance_miles') or 0)
        
        base_stops = []
        prev_dist = 0.0
        cum_time_min = 0
        
        for s in base_stops_raw:
            stop = dict(s)
            stop['distance_miles'] = float(stop['distance_miles']) if stop.get('distance_miles') is not None else None
            stop['elevation_gain'] = int(stop['elevation_gain']) if stop.get('elevation_gain') is not None else None
            stop['segment_time_min'] = int(stop['segment_time_min']) if stop.get('segment_time_min') is not None else None
            
            cur_dist = stop['distance_miles'] or 0.0
            seg_dist = round(cur_dist - prev_dist, 1)
            stop['seg_dist'] = seg_dist
            
            # Ft/mile
            elev = stop.get('elevation_gain') or 0
            stop['ft_per_mi'] = int(round(elev / seg_dist)) if seg_dist > 0 and elev > 0 else None
            
            # Avg speed
            seg_time = stop.get('segment_time_min') or 0
            stop['avg_speed'] = round(seg_dist / (seg_time / 60.0), 1) if seg_time > 0 and seg_dist > 0 else None
            
            # Cumulative time
            if seg_time:
                cum_time_min += seg_time
            stop['cum_time_min'] = cum_time_min
            
            # Time bank
            if cutoff_hours and total_distance > 0 and cur_dist > 0:
                fraction = cur_dist / total_distance
                bookend_time_min = round(fraction * cutoff_hours * 60)
                stop['time_bank_min'] = bookend_time_min - cum_time_min
            else:
                stop['time_bank_min'] = None
            
            # Difficulty score
            stop['difficulty_score'] = _compute_difficulty_score(stop['ft_per_mi'], stop.get('notes'))
            stop['difficulty_label'] = _difficulty_label(stop['difficulty_score'])
            
            prev_dist = cur_dist
            base_stops.append(stop)
    
    # Get public custom plans from other riders
    public_plans = get_public_custom_plans(base_plan['id'])

    # Attach break merging metadata for timeline layout
    base_stops, use_timeline = _attach_break_metadata(base_stops)
    if custom_stops:
        custom_stops, _ = _attach_break_metadata(custom_stops)

    return render_template('custom_ride_plan.html',
                           base_plan=base_plan,
                           base_stops=base_stops,
                           custom_plan=custom_plan,
                           custom_stops=custom_stops,
                           use_timeline=use_timeline,
                           public_plans=public_plans)


@riders_bp.route('/api/custom-plan/create', methods=['POST'])
@user_login_required
def api_create_custom_plan():
    """Create a new custom plan."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'User not linked to rider'}), 403
    
    data = request.json
    base_plan_id = data.get('base_plan_id')
    name = data.get('name')
    description = data.get('description')
    avg_moving_speed = data.get('avg_moving_speed')
    
    if not base_plan_id or not name:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    try:
        custom_plan_id = create_custom_plan(
            user['rider_id'], 
            base_plan_id, 
            name, 
            description,
            avg_moving_speed
        )
        return jsonify({'success': True, 'custom_plan_id': custom_plan_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>')
@user_login_required
def api_get_custom_plan(custom_plan_id):
    """Get custom plan details with merged stops."""
    try:
        custom_stops, custom_plan = get_merged_plan_stops(custom_plan_id)
        
        if not custom_plan:
            return jsonify({'success': False, 'error': 'Plan not found'}), 404
        
        # Convert to JSON-serializable format
        stops_data = []
        for stop in custom_stops:
            stop_dict = dict(stop)
            # Convert Decimal to float
            for key in ['distance_miles', 'avg_speed', 'avg_moving_speed']:
                if key in stop_dict and stop_dict[key] is not None:
                    stop_dict[key] = float(stop_dict[key])
            stops_data.append(stop_dict)
        
        plan_dict = dict(custom_plan)
        if 'avg_moving_speed' in plan_dict and plan_dict['avg_moving_speed']:
            plan_dict['avg_moving_speed'] = float(plan_dict['avg_moving_speed'])
        
        return jsonify({
            'success': True,
            'plan': plan_dict,
            'stops': stops_data
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/base-plan/stop/<int:stop_id>', methods=['PUT'])
@user_login_required
def api_update_base_stop(stop_id):
    """Admin-only: Update base plan stop."""
    import sys
    if not is_admin_user():
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json
    print(f"DEBUG: Updating stop {stop_id} with data: {data}", file=sys.stderr)
    
    try:
        from models import update_base_plan_stop
        success = update_base_plan_stop(stop_id, data)
        print(f"DEBUG: Update result: {success}", file=sys.stderr)
        return jsonify({'success': success})
    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>/stop/<int:stop_id>', methods=['PUT'])
@user_login_required
def api_update_custom_stop(custom_plan_id, stop_id):
    """Update timing, distance, or notes for a stop."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Verify ownership
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan or custom_plan['rider_id'] != user['rider_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json
    
    # Handle explicit None/null values vs. missing keys
    segment_time_min = data.get('segment_time_min') if 'segment_time_min' in data else None
    stop_duration_min = data.get('stop_duration_min') if 'stop_duration_min' in data else None
    stop_name = data.get('stop_name') if 'stop_name' in data else None
    location = data.get('location') if 'location' in data else None
    distance_miles = data.get('distance_miles') if 'distance_miles' in data else None
    elevation_gain = data.get('elevation_gain') if 'elevation_gain' in data else None
    notes = data.get('notes') if 'notes' in data else None
    
    print(f"[DEBUG] api_update_custom_stop: custom_plan_id={custom_plan_id}, stop_id={stop_id}")
    print(f"[DEBUG] Request data: {data}")
    print(f"[DEBUG] Explicit fields: {set(data.keys())}")
    
    try:
        success = update_custom_plan_stop(
            custom_plan_id, stop_id, 
            segment_time_min=segment_time_min,
            stop_duration_min=stop_duration_min,
            stop_name=stop_name,
            location=location,
            notes=notes,
            distance_miles=distance_miles,
            elevation_gain=elevation_gain,
            explicit_fields=set(data.keys())
        )
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>/stop/add', methods=['POST'])
@user_login_required
def api_add_custom_stop(custom_plan_id):
    """Add a custom stop."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Verify ownership
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan or custom_plan['rider_id'] != user['rider_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json
    location = data.get('location')
    stop_type = data.get('stop_type', 'waypoint')
    distance_miles = data.get('distance_miles')
    elevation_gain = data.get('elevation_gain', 0)
    segment_time_min = data.get('segment_time_min')
    after_stop_order = data.get('after_stop_order')
    notes = data.get('notes')
    
    if not location or distance_miles is None or after_stop_order is None:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    try:
        stop_id = add_custom_stop(
            custom_plan_id, location, stop_type, distance_miles,
            elevation_gain, after_stop_order, segment_time_min, notes
        )
        return jsonify({'success': True, 'stop_id': stop_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>/stop/<int:base_stop_id>/hide', methods=['POST'])
@user_login_required
def api_hide_base_stop(custom_plan_id, base_stop_id):
    """Hide a base stop in custom plan."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Verify ownership
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan or custom_plan['rider_id'] != user['rider_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        success = hide_base_stop(custom_plan_id, base_stop_id)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>/stop/<int:base_stop_id>/unhide', methods=['POST'])
@user_login_required
def api_unhide_base_stop(custom_plan_id, base_stop_id):
    """Unhide a previously hidden base stop."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Verify ownership
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan or custom_plan['rider_id'] != user['rider_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        success = unhide_base_stop(custom_plan_id, base_stop_id)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>/settings', methods=['PUT'])
@user_login_required
def api_update_custom_plan_settings(custom_plan_id):
    """Update custom plan settings."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Verify ownership
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan or custom_plan['rider_id'] != user['rider_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json
    name = data.get('name')
    description = data.get('description')
    is_public = data.get('is_public')
    avg_moving_speed = data.get('avg_moving_speed')
    
    try:
        success = update_custom_plan_settings(
            custom_plan_id, user['rider_id'],
            name, description, is_public, avg_moving_speed
        )
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>/apply-pace', methods=['POST'])
@user_login_required
def api_apply_pace_to_all_segments(custom_plan_id):
    """Apply pace adjustment to all segments, recalculating times."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Verify ownership
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan or custom_plan['rider_id'] != user['rider_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json
    avg_moving_speed = data.get('avg_moving_speed')
    
    if not avg_moving_speed or avg_moving_speed <= 0:
        return jsonify({'success': False, 'error': 'Invalid speed'}), 400
    
    try:
        # Get current merged stops
        custom_stops, _ = get_merged_plan_stops(custom_plan_id)
        
        # Apply pace adjustment
        adjusted_stops = apply_pace_adjustment(custom_stops, avg_moving_speed)
        
        # Update each stop's timing in the database
        from db import get_db
        import psycopg2.extras
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        for stop in adjusted_stops:
            if stop.get('is_modified') and not stop.get('is_custom_stop'):
                # This is a base stop with adjusted timing
                base_stop_id = stop.get('id')
                new_time = stop.get('segment_time_min')
                
                if base_stop_id and new_time:
                    # Check if override exists
                    cur.execute("""
                        SELECT id FROM custom_ride_plan_stop
                        WHERE custom_plan_id = %s AND base_stop_id = %s
                    """, (custom_plan_id, base_stop_id))
                    existing = cur.fetchone()
                    
                    if existing:
                        # Update existing override
                        cur.execute("""
                            UPDATE custom_ride_plan_stop
                            SET segment_time_min = %s
                            WHERE id = %s
                        """, (new_time, existing['id']))
                    else:
                        # Create new override
                        cur.execute("""
                            INSERT INTO custom_ride_plan_stop
                            (custom_plan_id, base_stop_id, stop_order, location, stop_type,
                             distance_miles, elevation_gain, segment_time_min)
                            SELECT %s, id, stop_order, location, stop_type,
                                   distance_miles, elevation_gain, %s
                            FROM ride_plan_stop
                            WHERE id = %s
                        """, (custom_plan_id, new_time, base_stop_id))
        
        # Save the avg_moving_speed setting
        cur.execute("""
            UPDATE custom_ride_plan
            SET avg_moving_speed = %s
            WHERE id = %s
        """, (avg_moving_speed, custom_plan_id))
        
        conn.commit()
        
        # Clear caches
        cache.delete_memoized(get_custom_plan_stops_raw, custom_plan_id)
        cache.delete_memoized(get_custom_plan_by_id, custom_plan_id)
        cache.delete_memoized(get_custom_plan, custom_plan['rider_id'], custom_plan['base_plan_id'])
        
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>/stop/<int:stop_id>/delete', methods=['DELETE'])
@user_login_required
def api_delete_custom_stop(custom_plan_id, stop_id):
    """Delete a stop from custom plan."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Verify ownership
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan or custom_plan['rider_id'] != user['rider_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json or {}
    is_custom_stop = data.get('is_custom_stop', False)
    
    try:
        if is_custom_stop:
            # Delete custom-added stop
            success = delete_custom_stop(stop_id, user['rider_id'])
            if not success:
                return jsonify({'success': False, 'error': 'Failed to delete stop'}), 400
        else:
            # For base stops, hide them permanently (user sees this as "delete")
            success = hide_base_stop(custom_plan_id, stop_id)
            if not success:
                return jsonify({'success': False, 'error': 'Failed to remove stop'}), 400
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/api/custom-plan/<int:custom_plan_id>', methods=['DELETE'])
@user_login_required
def api_delete_custom_plan(custom_plan_id):
    """Delete a custom plan."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        success = delete_custom_plan(custom_plan_id, user['rider_id'])
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@riders_bp.route('/ride-plan/<slug>/compare')
@user_login_required
def compare_ride_plans(slug):
    """Side-by-side comparison of base plan vs custom plan."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return redirect(url_for('auth.complete_profile'))
    
    rider_id = user['rider_id']
    
    # Get base plan
    base_plan = get_ride_plan_by_slug(slug)
    if not base_plan:
        abort(404)
    
    # Get custom plan
    custom_plan = get_custom_plan(rider_id, base_plan['id'])
    if not custom_plan:
        return redirect(url_for('riders.ride_plan_detail', slug=slug))
    
    # Load base stops and calculate cumulative times
    base_stops_raw = get_ride_plan_stops(base_plan['id'])
    
    # Calculate cumulative times for base stops
    from services.custom_plan_service import recalculate_cumulative_values
    base_stops = recalculate_cumulative_values(list(base_stops_raw), base_plan)
    
    # Load custom stops (merged, which excludes hidden stops)
    custom_stops_merged, custom_plan_data = get_merged_plan_stops(custom_plan['id'])
    
    # Recalculate cumulative times for custom stops
    custom_stops_merged = recalculate_cumulative_values(list(custom_stops_merged), custom_plan_data)
    
    # Get raw overrides to identify hidden stops
    custom_stops_raw = get_custom_plan_stops_raw(custom_plan['id'])
    hidden_stop_ids = {cs['base_stop_id'] for cs in custom_stops_raw 
                       if cs.get('is_hidden') and cs.get('base_stop_id')}
    
    # Build a comprehensive comparison list with all stops in order
    all_stops_for_comparison = []
    
    # Add all base stops (including hidden ones)
    for base_stop in base_stops:
        is_hidden = base_stop['id'] in hidden_stop_ids
        custom_stop = None
        if not is_hidden:
            custom_stop = next((s for s in custom_stops_merged if s.get('id') == base_stop['id']), None)
        
        all_stops_for_comparison.append({
            'base_stop': base_stop,
            'custom_stop': custom_stop,
            'is_hidden': is_hidden,
            'is_custom_only': False,
            'distance_miles': base_stop.get('distance_miles', 0)
        })
    
    # Add custom-only stops (new stops not in base plan)
    for custom_stop in custom_stops_merged:
        if custom_stop.get('is_custom_stop'):
            all_stops_for_comparison.append({
                'base_stop': None,
                'custom_stop': custom_stop,
                'is_hidden': False,
                'is_custom_only': True,
                'distance_miles': custom_stop.get('distance_miles', 0)
            })
    
    # Sort all stops by distance
    all_stops_for_comparison.sort(key=lambda x: float(x['distance_miles']))
    
    # Calculate cumulative times for removed stops by tracking custom plan cumulative at each distance
    prev_custom_cumulative = 0
    for i, item in enumerate(all_stops_for_comparison):
        if item['is_hidden']:
            # For removed stops, use the previous stop's cumulative (no time added since we're not stopping)
            item['custom_cumulative_at_distance'] = prev_custom_cumulative
        elif item['custom_stop'] and item['custom_stop'].get('cum_time_min'):
            # For existing/modified/new stops, use the actual cumulative time
            item['custom_cumulative_at_distance'] = item['custom_stop']['cum_time_min']
            prev_custom_cumulative = item['custom_stop']['cum_time_min']
        else:
            item['custom_cumulative_at_distance'] = None
    
    # Generate comparison
    comparison = compare_plans(base_stops, custom_stops_merged)
    
    return render_template('ride_plan_compare.html',
                           base_plan=base_plan,
                           base_stops=base_stops,
                           custom_plan=custom_plan_data,
                           custom_stops=custom_stops_merged,
                           hidden_stop_ids=hidden_stop_ids,
                           comparison=comparison,
                           all_stops_for_comparison=all_stops_for_comparison)


@riders_bp.route('/api/custom-plan/<int:source_plan_id>/clone', methods=['POST'])
@user_login_required
def api_clone_custom_plan(source_plan_id):
    """Clone another rider's public custom plan."""
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json
    new_name = data.get('name')
    
    try:
        new_plan_id = clone_custom_plan(source_plan_id, user['rider_id'], new_name)
        if new_plan_id:
            return jsonify({'success': True, 'custom_plan_id': new_plan_id})
        else:
            return jsonify({'success': False, 'error': 'Plan not found or not public'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
