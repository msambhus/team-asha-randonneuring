"""Rider routes: season view, individual profiles, profile edit, upcoming brevets, ride plans."""
from flask import Blueprint, render_template, abort, request, redirect, url_for
from models import (get_season_by_name, get_riders_for_season, get_active_riders_for_season,
                    get_rides_for_season, get_participation_matrix, get_season_stats,
                    get_rider_by_rusa, get_rider_participation, get_rider_career_stats,
                    get_rider_season_stats, get_all_seasons, get_current_season,
                    detect_sr_for_rider_season, get_rider_total_srs,
                    get_all_rider_season_stats, detect_sr_for_all_riders_in_season,
                    get_upcoming_rusa_events, update_rider_profile,
                    get_pbp_finishers,
                    get_all_ride_plans, get_ride_plan_by_slug, get_ride_plan_stops)
from auth import login_required
from datetime import date
import re

riders_bp = Blueprint('riders', __name__)

# Map season name to display label
SEASON_LABELS = {
    '2025-2026': '2025/2026 Season',
    '2022-2023': '2022-2023 Season',
    '2021-2022': '2021-2022 Season',
}


@riders_bp.route('/riders/<season_name>')
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

    today = date.today().isoformat()
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
                             if rid in past_ride_ids and p['status'].lower() == 'yes')
            kms_count = sum(ri['distance_km'] for ri in past_rides
                           if ri['id'] in part and part[ri['id']]['status'].lower() == 'yes')

        if rides_count > 0 or not is_current:
            rider_data.append({
                'rider': r,
                'rides': rides_count,
                'kms': kms_count,
                'sr_count': sr_n,
                'participation': matrix.get(r['id'], {}),
            })

    # Sort by rides completed descending, then name
    rider_data.sort(key=lambda x: (-x['rides'], x['rider']['first_name']))

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


def _match_plans_to_events(events, plans):
    """Attach plan_slug to RUSA events by matching route names.
    Requires at least 2 meaningful keyword matches to avoid false positives,
    unless there's a distinctive word match (e.g. 'healdsburg', 'hopland')."""
    for event in events:
        e_words = _normalize_route(event.get('route_name', ''))
        best_slug = None
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
        event['plan_slug'] = best_slug


@riders_bp.route('/riders/<season_name>/upcoming')
def upcoming_brevets(season_name):
    season = get_season_by_name(season_name)
    if not season:
        abort(404)

    current = get_current_season()
    is_current = current and current['id'] == season['id']
    if not is_current:
        return redirect(url_for('riders.season_riders', season_name=season_name))

    rusa_events = get_upcoming_rusa_events()

    rides = get_rides_for_season(season['id'])
    today = date.today().isoformat()
    future_rides = [r for r in rides if r['date'] and r['date'] > today]

    # Build ride plan lookup for RUSA events
    plans = get_all_ride_plans()
    _match_plans_to_events(rusa_events, plans)

    # Region color map
    region_colors = {
        'San Francisco': '#e74c3c',
        'Davis': '#2ecc71',
        'Santa Cruz': '#3498db',
    }

    # Build distance filter from actual event data
    distances = sorted(set(e['distance_km'] for e in rusa_events if e.get('distance_km')))

    label = SEASON_LABELS.get(season_name, f'{season_name} Season')

    return render_template('upcoming_brevets.html',
                           season=season,
                           season_label=label,
                           rusa_events=rusa_events,
                           future_rides=future_rides,
                           is_current=is_current,
                           region_colors=region_colors,
                           distances=distances)


@riders_bp.route('/rider/<int:rusa_id>')
def rider_profile(rusa_id):
    rider = get_rider_by_rusa(rusa_id)
    if not rider:
        abort(404)

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

    return render_template('rider_profile.html',
                           rider=rider,
                           season_data=season_data,
                           career_rides=career_rides,
                           career_kms=career_kms,
                           total_srs=total_srs)


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
        return redirect(url_for('riders.rider_profile', rusa_id=rusa_id))

    return render_template('rider_edit.html', rider=rider)


@riders_bp.route('/ride-plans')
def ride_plans_index():
    plans = get_all_ride_plans()
    return render_template('ride_plans.html', plans=plans)


@riders_bp.route('/ride-plan/<slug>')
def ride_plan_detail(slug):
    plan = get_ride_plan_by_slug(slug)
    if not plan:
        abort(404)
    raw_stops = get_ride_plan_stops(plan['id'])
    # Convert Decimal types to float for Jinja2 arithmetic
    plan = dict(plan)
    plan['total_distance_miles'] = float(plan.get('total_distance_miles') or 0)
    plan['total_elevation_ft'] = int(plan.get('total_elevation_ft') or 0)
    stops = []
    for s in raw_stops:
        d = dict(s)
        d['distance_miles'] = float(d['distance_miles']) if d.get('distance_miles') is not None else None
        d['elevation_gain'] = int(d['elevation_gain']) if d.get('elevation_gain') is not None else None
        d['segment_time_min'] = int(d['segment_time_min']) if d.get('segment_time_min') is not None else None
        stops.append(d)
    return render_template('ride_plan_detail.html', plan=plan, stops=stops)
