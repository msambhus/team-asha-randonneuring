"""Main routes: home, about, resources."""
from flask import Blueprint, render_template
from models import (get_all_time_stats, get_all_seasons, get_current_season,
                    get_season_stats, get_upcoming_rusa_events, get_upcoming_rides)

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    stats = get_all_time_stats()
    seasons = get_all_seasons()
    current = get_current_season()
    current_stats = get_season_stats(current['id']) if current else {}

    # Season summaries for cards
    season_summaries = []
    for s in seasons:
        ss = get_season_stats(s['id'])
        season_summaries.append({
            'name': s['name'],
            'active_riders': ss['active_riders'],
            'total_rides': ss['total_rides'],
            'total_kms': ss['total_kms'],
            'sr_count': ss['sr_count'],
            'sr_rider_count': ss['sr_rider_count'],
            'is_current': current and s['id'] == current['id'],
        })

    return render_template('index.html',
                           stats=stats,
                           current_stats=current_stats,
                           season_summaries=season_summaries)


@main_bp.route('/about')
def about():
    return render_template('about.html')


@main_bp.route('/resources')
def resources():
    return render_template('resources.html')


@main_bp.route('/upcoming')
def upcoming():
    rides = get_upcoming_rides()
    rusa_events = get_upcoming_rusa_events()
    return render_template('upcoming.html', rides=rides, rusa_events=rusa_events)
