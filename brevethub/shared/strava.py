"""Shared Strava HTTP layer — framework-free and epoch-native.

Pure functions for Strava OAuth token exchange/refresh, activity fetching, the
activity transform, and a per-rider activity summary. Every piece of Strava
configuration (client id/secret, token URL, API base) is passed as an explicit
keyword argument; nothing here reads Flask application globals or imports any Team
Asha module, so both the Team Asha app (through ``services/strava.py``) and
BrevetHub import one implementation. ``expires_at`` is a Unix epoch integer in
and out, exactly as Strava returns it — any epoch↔TIMESTAMPTZ conversion is the
caller's responsibility (BrevetHub does it at its DB boundary).

``tests/brevethub/test_shared_isolation.py`` fails the build if this module ever
imports a Team Asha module or reaches for a Flask app global.
"""
import json
import time

import requests


def exchange_code_for_token(code, *, client_id, client_secret, token_url):
    """Exchange an authorization ``code`` for access/refresh tokens.

    Returns the raw Strava token payload (dict with ``athlete``, ``access_token``,
    ``refresh_token``, ``expires_at`` as an epoch integer).
    """
    if not client_secret:
        raise Exception("STRAVA_CLIENT_SECRET not configured — add it to environment variables")

    resp = requests.post(
        token_url,
        data={
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'grant_type': 'authorization_code',
        },
        timeout=10,
    )
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise Exception(f"Strava token error ({resp.status_code}): {detail}")
    return resp.json()


def refresh_access_token(refresh_token, *, client_id, client_secret, token_url):
    """Exchange a ``refresh_token`` for a fresh access token.

    Returns the raw Strava token payload (``access_token``, ``refresh_token``,
    ``expires_at`` as an epoch integer).
    """
    resp = requests.post(
        token_url,
        data={
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        },
        timeout=10,
    )
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise Exception(f"Strava token refresh error ({resp.status_code}): {detail}")
    return resp.json()


def fetch_activities(access_token, *, api_base, after_epoch=None, before_epoch=None, per_page=100):
    """Fetch activities for the athlete owning ``access_token``, paginating.

    Args:
        access_token: a valid Strava access token (bearer).
        api_base: Strava API base URL (e.g. https://www.strava.com/api/v3).
        after_epoch: only activities after this Unix timestamp (default: 1 year ago).
        before_epoch: only activities before this Unix timestamp (default: now).
        per_page: activities per API page (max 200).

    Returns:
        list of raw activity dicts from the Strava API.
    """
    if after_epoch is None:
        after_epoch = int(time.time()) - (365 * 24 * 3600)  # 1 year

    all_activities = []
    page = 1

    while True:
        params = {
            'after': after_epoch,
            'per_page': per_page,
            'page': page,
        }
        if before_epoch is not None:
            params['before'] = before_epoch

        resp = requests.get(
            f"{api_base}/athlete/activities",
            headers={'Authorization': f'Bearer {access_token}'},
            params=params,
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


# The Strava activity-streams the ride-analysis engine consumes, as the API's
# `keys` param. Mirrors shared.strava_analysis._STREAM_KEYS.
ACTIVITY_STREAM_KEYS = 'time,distance,velocity_smooth,heartrate,watts,cadence,altitude,grade_smooth,latlng'


def fetch_activity_streams(access_token, activity_id, *, api_base, keys=None,
                           timeout=15):
    """Fetch one activity's data streams from the Strava API.

    Args:
        access_token: a valid Strava access token (bearer).
        activity_id: the Strava activity id (may exceed 32 bits — a plain int).
        api_base: Strava API base URL (e.g. https://www.strava.com/api/v3).
        keys: comma-separated stream keys to request (defaults to the full
            analysis set in ``ACTIVITY_STREAM_KEYS``).
        timeout: per-request timeout in seconds.

    Returns:
        dict mapping each stream type to its data list, e.g.
        ``{'time': [...], 'distance': [...], 'latlng': [[lat, lng], ...], ...}``.
        Strava returns a list of ``{type, data, ...}`` objects; this flattens it to
        ``{type: data}``.

    Raises:
        Exception on a 429 rate limit (a distinct message the caller can surface),
        and via ``raise_for_status`` on any other non-OK response — so a private/
        missing activity (404) or auth failure (401) never returns partial data.
    """
    resp = requests.get(
        f"{api_base}/activities/{activity_id}/streams",
        headers={'Authorization': f'Bearer {access_token}'},
        params={'keys': keys or ACTIVITY_STREAM_KEYS, 'key_type': 'time'},
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise Exception("Strava rate limit exceeded. Please try again later.")
    resp.raise_for_status()
    streams = {}
    for s in resp.json():
        streams[s['type']] = s['data']
    return streams


def transform_activity(activity, rider_id):
    """Map a raw Strava API activity into a flat, storage-shaped dict."""
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
        'average_cadence':      activity.get('average_cadence'),
        'average_temp':         activity.get('average_temp'),
        'calories':             activity.get('calories'),
        'pr_count':             activity.get('pr_count'),
        'achievement_count':    activity.get('achievement_count'),
        'gear_id':              activity.get('gear_id'),
        'elev_high':            activity.get('elev_high'),
        'elev_low':             activity.get('elev_low'),
        'trainer':              activity.get('trainer', False),
        'commute':              activity.get('commute', False),
        'workout_type':         activity.get('workout_type'),
        'map_summary_polyline': (activity.get('map') or {}).get('summary_polyline'),
        'start_latlng':         json.dumps(activity['start_latlng']) if activity.get('start_latlng') else None,
        'end_latlng':           json.dumps(activity['end_latlng']) if activity.get('end_latlng') else None,
    }


# Strava activity types that count as a ride for a randonneuring club.
CYCLING_TYPES = ('Ride', 'VirtualRide', 'EBikeRide')


def summarize_activities(activities):
    """Summarize transformed cycling activities into per-rider totals.

    Args:
        activities: list of dicts as produced by :func:`transform_activity`.

    Returns:
        dict: ride count, distance (km), elevation (m), moving time (hours).
        Non-cycling activities are ignored.
    """
    rides = [a for a in activities if a.get('activity_type') in CYCLING_TYPES]
    distance_m = sum((a.get('distance') or 0) for a in rides)
    elevation_m = sum((a.get('total_elevation_gain') or 0) for a in rides)
    moving_s = sum((a.get('moving_time') or 0) for a in rides)
    return {
        'rides': len(rides),
        'distance_km': round(distance_m / 1000.0, 1),
        'elevation_m': round(elevation_m),
        'moving_hours': round(moving_s / 3600.0, 1),
    }


def deauthorize_strava(access_token):
    """Revoke a Strava access token (best-effort; never raises)."""
    try:
        requests.post(
            'https://www.strava.com/oauth/deauthorize',
            data={'access_token': access_token},
            timeout=10,
        )
    except Exception:
        pass  # Best-effort revocation
