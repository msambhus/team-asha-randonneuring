"""Garmin LiveTrack ingestion service.

Garmin LiveTrack has **no official API**. This module talks to the same
unofficial JSON endpoints the LiveTrack web page uses, isolated here so the
single point of breakage (and the single point to mock in tests) is one file.

A LiveTrack share link looks like:
    https://livetrack.garmin.com/session/<session_id>/token/<token>

`parse_session(url)` pulls the {session_id, token} out of that link.
`fetch_positions(token, session_id)` polls the trackpoints endpoint and returns
a normalized list of {lat, lng, recorded_at} dicts.

Design notes:
  - All network access uses `requests` with a timeout and explicit handling of
    404 / 401 / 429 / 5xx, mirroring services/rwgps.py.
  - The JSON shape is parsed defensively (Garmin has changed field names over
    time): we accept position under `position.{lat,lon}`, top-level
    `latitude/longitude`, or `lat/lng`, and timestamps under `dateTime`/
    `timestamp`/`recorded_at`.
  - Callers treat this as fail-soft: on any error we raise a descriptive
    exception that the cron handler catches per-rider so one bad session never
    breaks the batch.
"""
import re
from datetime import datetime, timezone

import requests as http_requests
from flask import current_app

# Endpoint templates kept as module constants so the unofficial URLs can be
# swapped in one place if Garmin changes them.
_SESSION_URL_RE = re.compile(
    r'livetrack\.garmin\.com/session/(?P<session_id>[^/?#]+)/token/(?P<token>[^/?#]+)',
    re.IGNORECASE,
)
_TRACKPOINTS_URL = (
    'https://livetrack.garmin.com/services/session/{session_id}'
    '/token/{token}/trackpoints'
)
_REQUEST_TIMEOUT = 15


def parse_session(url):
    """Extract {'session_id', 'token'} from a Garmin LiveTrack share URL.

    Returns None if the URL is empty or not a recognizable LiveTrack link.
    """
    if not url or not isinstance(url, str):
        return None
    match = _SESSION_URL_RE.search(url.strip())
    if not match:
        return None
    return {
        'session_id': match.group('session_id'),
        'token': match.group('token'),
    }


def _parse_timestamp(raw):
    """Best-effort parse of a Garmin timestamp into a tz-aware datetime.

    Accepts ISO-8601 strings (with trailing 'Z') or epoch milliseconds.
    Returns None if unparseable.
    """
    if raw is None:
        return None
    # Epoch milliseconds (int or numeric string)
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.isdigit():
            try:
                return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                return None
        try:
            return datetime.fromisoformat(s.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def _extract_point(raw_point):
    """Normalize one raw Garmin trackpoint into {lat, lng, recorded_at} or None.

    Tolerates the several shapes Garmin's JSON has used over time.
    """
    if not isinstance(raw_point, dict):
        return None

    # Coordinates: position.{lat,lon} | latitude/longitude | lat/lng
    lat = lng = None
    position = raw_point.get('position')
    if isinstance(position, dict):
        lat = position.get('lat', position.get('latitude'))
        lng = position.get('lon', position.get('lng', position.get('longitude')))
    if lat is None:
        lat = raw_point.get('latitude', raw_point.get('lat'))
    if lng is None:
        lng = raw_point.get('longitude', raw_point.get('lng', raw_point.get('lon')))

    if lat is None or lng is None:
        return None
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return None
    # Reject obviously invalid coordinates.
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return None

    recorded_at = _parse_timestamp(
        raw_point.get('dateTime')
        or raw_point.get('timestamp')
        or raw_point.get('recorded_at')
    )

    return {'lat': lat, 'lng': lng, 'recorded_at': recorded_at}


def fetch_positions(token, session_id):
    """Poll the LiveTrack trackpoints endpoint for one session.

    Returns a list of {lat, lng, recorded_at} dicts ordered as Garmin returns
    them (chronological). Raises a descriptive Exception on any HTTP/parse
    failure so the caller can fail soft per-rider.
    """
    if not token or not session_id:
        raise ValueError('fetch_positions requires both token and session_id')

    url = _TRACKPOINTS_URL.format(session_id=session_id, token=token)
    try:
        resp = http_requests.get(url, timeout=_REQUEST_TIMEOUT)
    except http_requests.Timeout:
        raise Exception(f'Garmin LiveTrack request timed out for session {session_id}.')
    except http_requests.RequestException as exc:
        raise Exception(f'Garmin LiveTrack request failed for session {session_id}: {exc}')

    if resp.status_code == 404:
        raise Exception(f'Garmin LiveTrack session {session_id} not found (404) — likely ended/expired.')
    if resp.status_code in (401, 403):
        raise Exception(f'Garmin LiveTrack session {session_id} unauthorized ({resp.status_code}) — token invalid/expired.')
    if resp.status_code == 429:
        raise Exception('Garmin LiveTrack rate limited (429). Back off and retry later.')
    if not resp.ok:
        raise Exception(f'Garmin LiveTrack error (HTTP {resp.status_code}) for session {session_id}.')

    try:
        data = resp.json()
    except ValueError:
        raise Exception(f'Garmin LiveTrack returned non-JSON for session {session_id}.')

    # The payload has been seen as a bare list, or wrapped under trackPoints/
    # trackPointRequest. Be liberal in what we accept.
    raw_points = None
    if isinstance(data, list):
        raw_points = data
    elif isinstance(data, dict):
        raw_points = (
            data.get('trackPoints')
            or data.get('trackpoints')
            or (data.get('trackPointRequest') or {}).get('trackPoints')
            or []
        )
    if not isinstance(raw_points, list):
        raw_points = []

    positions = []
    for raw in raw_points:
        point = _extract_point(raw)
        if point is not None:
            positions.append(point)

    current_app.logger.info(
        'Garmin LiveTrack session %s: %d/%d points parsed',
        session_id, len(positions), len(raw_points),
    )
    return positions
