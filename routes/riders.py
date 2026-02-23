"""Rider routes: season view, individual profiles, profile edit."""
from flask import Blueprint, render_template, abort, request, redirect, url_for
from models import (get_season_by_name, get_riders_for_season, get_rides_for_season,
                    get_participation_matrix, get_season_stats, get_rider_by_rusa,
                    get_rider_participation, get_rider_career_stats, get_rider_season_stats,
                    get_all_seasons, get_current_season, detect_sr_for_rider_season,
                    get_rider_total_srs, get_upcoming_rusa_events, update_rider_profile)
from auth import login_required
from datetime import date

riders_bp = Blueprint('riders', __name__)

# Map season name to display label
SEASON_LABELS = {
    '2025-2026': '2025/2026 Season',
    '2022-2023': '2022-2023 Season',
    '2021-2022': '2021-2022 Season',
}


@riders_bp.route('/riders/<season_name>')
def season_riders(season_name):
    season = get_season_by_name(season_name)
    if not season:
        abort(404)

    riders = get_riders_for_season(season['id'])
    rides = get_rides_for_season(season['id'])
    matrix = get_participation_matrix(season['id'])
    stats = get_season_stats(season['id'])
    current = get_current_season()
    is_current = current and current['id'] == season['id']

    today = date.today().isoformat()
    past_rides = [r for r in rides if r['date'] < today]
    future_rides = [r for r in rides if r['date'] >= today]

    # Compute per-rider stats for display
    rider_data = []
    for r in riders:
        s = get_rider_season_stats(r['id'], season['id'])
        sr_n = detect_sr_for_rider_season(r['id'], season['id'], date_filter=is_current)
        rider_data.append({
            'rider': r,
            'rides': s['rides'],
            'kms': s['kms'],
            'sr_count': sr_n,
            'participation': matrix.get(r['id'], {}),
        })

    # RUSA events for upcoming calendar
    rusa_events = get_upcoming_rusa_events() if is_current else []

    # Region color map
    region_colors = {
        'San Francisco': '#e74c3c',
        'Davis': '#2ecc71',
        'Santa Cruz': '#3498db',
        'Team Asha': '#ff6b00',
    }

    label = SEASON_LABELS.get(season_name, f'{season_name} Season')

    return render_template('riders.html',
                           season=season,
                           season_label=label,
                           riders=rider_data,
                           past_rides=past_rides,
                           future_rides=future_rides,
                           stats=stats,
                           is_current=is_current,
                           rusa_events=rusa_events,
                           region_colors=region_colors)


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
