"""Strava API service — OAuth token exchange, refresh, and activity fetching."""
import time
import requests as http_requests
from flask import current_app


def exchange_code_for_token(code):
    """Exchange authorization code for access/refresh tokens.

    Returns:
        dict with athlete, access_token, refresh_token, expires_at
    """
    resp = http_requests.post(
        current_app.config['STRAVA_TOKEN_URL'],
        data={
            'client_id': current_app.config['STRAVA_CLIENT_ID'],
            'client_secret': current_app.config['STRAVA_CLIENT_SECRET'],
            'code': code,
            'grant_type': 'authorization_code',
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _get_valid_token(connection):
    """Return a valid access token, refreshing if expired.

    Args:
        connection: dict with access_token, refresh_token, expires_at, rider_id

    Returns:
        str: valid access_token

    Side effect:
        Updates strava_connection row if token was refreshed.
    """
    if connection['expires_at'] > time.time() + 60:  # 60s buffer
        return connection['access_token']

    # Token expired or about to expire — refresh
    resp = http_requests.post(
        current_app.config['STRAVA_TOKEN_URL'],
        data={
            'client_id': current_app.config['STRAVA_CLIENT_ID'],
            'client_secret': current_app.config['STRAVA_CLIENT_SECRET'],
            'grant_type': 'refresh_token',
            'refresh_token': connection['refresh_token'],
        },
        timeout=10,
    )
    resp.raise_for_status()
    token_data = resp.json()

    # Persist new tokens
    from models import update_strava_tokens
    update_strava_tokens(
        rider_id=connection['rider_id'],
        access_token=token_data['access_token'],
        refresh_token=token_data['refresh_token'],
        expires_at=token_data['expires_at'],
    )

    return token_data['access_token']


def fetch_activities(connection, after_epoch=None, per_page=100):
    """Fetch activities from Strava API.

    Args:
        connection: strava_connection row dict
        after_epoch: Unix timestamp to fetch activities after (default: 4 weeks ago)
        per_page: Activities per API page (max 200)

    Returns:
        list of activity dicts from Strava API
    """
    token = _get_valid_token(connection)

    if after_epoch is None:
        after_epoch = int(time.time()) - (28 * 24 * 3600)  # 4 weeks

    all_activities = []
    page = 1

    while True:
        resp = http_requests.get(
            f"{current_app.config['STRAVA_API_BASE']}/athlete/activities",
            headers={'Authorization': f'Bearer {token}'},
            params={
                'after': after_epoch,
                'per_page': per_page,
                'page': page,
            },
            timeout=15,
        )
        if resp.status_code == 429:
            raise Exception("Strava rate limit exceeded. Please try again later.")
        resp.raise_for_status()
        activities = resp.json()

        if not activities:
            break

        all_activities.extend(activities)

        if len(activities) < per_page:
            break
        page += 1

    return all_activities


def transform_activity(activity, rider_id):
    """Transform Strava API activity into DB row dict."""
    strava_id = activity['id']
    return {
        'rider_id': rider_id,
        'strava_activity_id': strava_id,
        'name': activity.get('name'),
        'activity_type': activity.get('type'),
        'distance': activity.get('distance'),
        'moving_time': activity.get('moving_time'),
        'elapsed_time': activity.get('elapsed_time'),
        'total_elevation_gain': activity.get('total_elevation_gain'),
        'start_date': activity.get('start_date'),
        'start_date_local': activity.get('start_date_local'),
        'average_heartrate': activity.get('average_heartrate'),
        'max_heartrate': activity.get('max_heartrate'),
        'has_heartrate': activity.get('has_heartrate', False),
        'average_watts': activity.get('average_watts'),
        'max_watts': activity.get('max_watts'),
        'weighted_average_watts': activity.get('weighted_average_watts'),
        'kilojoules': activity.get('kilojoules'),
        'device_watts': activity.get('device_watts', False),
        'average_speed': activity.get('average_speed'),
        'max_speed': activity.get('max_speed'),
        'suffer_score': activity.get('suffer_score'),
        'strava_url': f'https://www.strava.com/activities/{strava_id}',
    }


def sync_rider_activities(rider_id):
    """Pull last 4 weeks of activities for a rider and upsert into DB.

    Returns:
        int: number of activities synced
    """
    from models import get_strava_connection, upsert_strava_activity, update_strava_last_sync

    connection = get_strava_connection(rider_id)
    if not connection:
        return 0

    activities = fetch_activities(connection)
    count = 0
    for activity in activities:
        row = transform_activity(activity, rider_id)
        upsert_strava_activity(row)
        count += 1

    update_strava_last_sync(rider_id)
    return count


def deauthorize_strava(access_token):
    """Revoke Strava access token (best-effort)."""
    try:
        http_requests.post(
            'https://www.strava.com/oauth/deauthorize',
            data={'access_token': access_token},
            timeout=10,
        )
    except Exception:
        pass  # Best-effort revocation
