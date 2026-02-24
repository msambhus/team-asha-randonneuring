"""Main routes: home, about, resources, feedback."""
import requests as http_requests
from flask import Blueprint, render_template, request, jsonify, current_app
from models import (get_all_time_stats, get_all_seasons, get_current_season,
                    get_season_stats, get_upcoming_rusa_events, get_upcoming_rides)

main_bp = Blueprint('main', __name__)


def get_mock_data():
    """Return mock data for testing without database."""
    return {
        'stats': {
            'riders': 42,
            'rides': 156,
            'kms': 87500,
            'srs': 18
        },
        'season_summaries': [
            {
                'name': '2025-2026',
                'active_riders': 25,
                'total_rides': 48,
                'total_kms': 28500,
                'sr_count': 5,
                'sr_rider_count': 8,
                'is_current': True,
            },
            {
                'name': '2022-2023',
                'active_riders': 22,
                'total_rides': 62,
                'total_kms': 35200,
                'sr_count': 7,
                'sr_rider_count': 11,
                'is_current': False,
            },
            {
                'name': '2021-2022',
                'active_riders': 18,
                'total_rides': 46,
                'total_kms': 23800,
                'sr_count': 4,
                'sr_rider_count': 6,
                'is_current': False,
            }
        ],
        'current_stats': {}
    }


@main_bp.route('/')
def index():
    try:
        stats = get_all_time_stats()
        seasons = get_all_seasons()
        current = get_current_season()
        current_stats = get_season_stats(current['id'], past_only=True) if current else {}

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
    except Exception as e:
        # Use mock data if database is not available
        print(f"Database not available, using mock data: {e}")
        mock = get_mock_data()
        return render_template('index.html',
                               stats=mock['stats'],
                               current_stats=mock['current_stats'],
                               season_summaries=mock['season_summaries'])


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


@main_bp.route('/api/feedback', methods=['POST'])
def submit_feedback():
    """Create a Linear ticket from website feedback form."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    text = (data.get('feedback') or '').strip()

    if not name or not email or not text:
        return jsonify({'error': 'All fields are required'}), 400

    api_key = current_app.config.get('LINEAR_API_KEY')
    team_id = current_app.config.get('LINEAR_TEAM_ID')
    if not api_key or not team_id:
        return jsonify({'error': 'Feedback service is temporarily unavailable'}), 503

    # Build title from first 60 chars of feedback
    short_text = text[:60] + ('...' if len(text) > 60 else '')
    title = f"Website Feedback: {short_text}"

    description = f"**From:** {name} ({email})\n\n{text}"

    mutation = """
    mutation($title: String!, $description: String!, $teamId: String!) {
      issueCreate(input: {
        teamId: $teamId
        title: $title
        description: $description
      }) {
        success
        issue { id identifier url }
      }
    }
    """

    try:
        resp = http_requests.post(
            'https://api.linear.app/graphql',
            json={
                'query': mutation,
                'variables': {
                    'title': title,
                    'description': description,
                    'teamId': team_id,
                }
            },
            headers={
                'Content-Type': 'application/json',
                'Authorization': api_key,
            },
            timeout=10,
        )
        result = resp.json()
        if result.get('data', {}).get('issueCreate', {}).get('success'):
            issue = result['data']['issueCreate']['issue']
            print(f"Feedback ticket created: {issue['identifier']} — {title}")
            return jsonify({'success': True, 'ticket': issue['identifier']})
        else:
            print(f"Linear API error: {result}")
            return jsonify({'error': 'Failed to create ticket'}), 500
    except Exception as e:
        print(f"Feedback submission error: {e}")
        return jsonify({'error': 'Failed to create ticket'}), 500
