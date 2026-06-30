"""Garmin LiveTrack ingestion service.

Garmin LiveTrack has **no official API**. This module scrapes the same public
share page a human would open, isolated here so the single point of breakage
(and the single point to mock in tests) is one file.

A LiveTrack share link looks like:
    https://livetrack.garmin.com/session/<session_id>/token/<token>

`parse_session(url)` pulls the {session_id, token} out of that link.
`fetch_positions(token, session_id)` loads the share page and returns a
normalized list of {lat, lng, recorded_at, ...} dicts.

Design notes:
  - Garmin rebuilt LiveTrack as a Next.js app and **deleted** the old
    unofficial REST endpoint (`/services/session/.../trackpoints`, now a hard
    404). The live trackpoints are instead **server-side-rendered into the
    share-page HTML** as an embedded (RSC/flight) JSON payload. So we fetch the
    share page itself (with a browser User-Agent) and pull the embedded
    `"trackPoints":[ ... ]` arrays back out.
  - All network access uses `requests` with a timeout and explicit handling of
    404 / 401 / 429 / 5xx, mirroring services/rwgps.py.
  - The point shape is parsed defensively (Garmin has changed field names over
    time): we accept position under `position.{lat,lon}`, top-level
    `latitude/longitude`, or `lat/lng`, and timestamps under `dateTime`/
    `timestamp`/`recorded_at`.
  - Callers treat this as fail-soft: on any error we raise a descriptive
    exception that the cron handler catches per-rider so one bad session never
    breaks the batch.
"""
import json
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
# Public share page (a real browser URL). Garmin server-renders the trackpoints
# into this page's HTML; we scrape them back out of `_extract_trackpoints_html`.
_SHARE_PAGE_URL = (
    'https://livetrack.garmin.com/session/{session_id}/token/{token}'
)
# A browser-like User-Agent: the share page is a normal web page and a bare
# requests UA is more likely to be challenged/blocked. Keep the Chrome version
# reasonably current so the request doesn't read as an ancient client.
_BROWSER_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
)
# Full browser-like header set sent with the share-page GET. These mirror what
# Chrome sends for a top-level navigation, so the request looks like a person
# opening the public share link rather than a scraper. The Sec-Fetch-* values
# are kept internally consistent with a document navigation (inconsistent values
# read as MORE bot-like). We deliberately do NOT set Accept-Encoding: `requests`
# negotiates gzip/deflate and decodes them automatically, but would not decode
# Brotli ('br') unless the brotli package is installed — advertising it here
# could yield an undecodable body and break trackpoint parsing.
_BROWSER_HEADERS = {
    'User-Agent': _BROWSER_UA,
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;q=0.9,'
        'image/avif,image/webp,image/apng,*/*;q=0.8'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-User': '?1',
    'Sec-Fetch-Dest': 'document',
}
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


def _num(value, cast):
    """Best-effort cast; None on failure."""
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _extract_point(raw_point):
    """Normalize one raw Garmin trackpoint into a dict or None.

    Returns {lat, lng, recorded_at, speed, heart_rate, power, cadence}; the
    fitness fields are None when Garmin doesn't include them (common — they
    depend on paired sensors). Tolerates the several shapes Garmin's JSON has
    used over time.
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

    # Fitness data may sit at the top level or nested under fitnessPointData.
    fit = raw_point.get('fitnessPointData')
    fit = fit if isinstance(fit, dict) else {}

    def pick(*keys):
        for src in (raw_point, fit):
            for k in keys:
                if src.get(k) is not None:
                    return src[k]
        return None

    return {
        'lat': lat, 'lng': lng, 'recorded_at': recorded_at,
        'speed': _num(pick('speedMetersPerSec', 'speed', 'speedMetersPerSecond'), float),
        'heart_rate': _num(pick('heartRate', 'heartRateBeatsPerMin', 'heart_rate'), int),
        'power': _num(pick('power', 'powerWatts'), int),
        'cadence': _num(pick('cadence', 'cadenceCyclesPerMin'), int),
    }


def _extract_trackpoints_html(html):
    """Pull every embedded `"trackPoints":[ ... ]` array out of the share page.

    Garmin's Next.js app server-renders the session's trackpoints into the page
    HTML as an escaped JSON (RSC/flight) payload — e.g.
    ``...trackPoints\\":[{\\"dateTime\\":...,\\"position\\":{...}}...]...``. We
    locate each `"trackPoints":[` marker, bracket-match the array out of the
    surrounding JS string (respecting the `\\"`/`\\\\` escaping), un-escape it,
    and json.loads the result. Returns a flat list of raw point dicts (caller
    normalizes via `_extract_point`). Tolerant of zero matches → [].
    """
    if not html:
        return []

    decoder = json.JSONDecoder()
    raw_points = []
    # `\"trackPoints\":[`  — the key is itself escaped inside the JS string.
    for marker in re.finditer(r'trackPoints\\":\[', html):
        start = marker.end() - 1  # index of the opening '['
        # Reverse the JS-string escaping (\" -> ", \\ -> \) from the array start
        # to EOF, then let the JSON decoder find the array's end via raw_decode
        # (it stops at the first complete value and ignores trailing data). Using
        # the decoder — rather than counting raw brackets — means a literal
        # '['/']' inside a string value can never mis-terminate the array.
        candidate = html[start:].replace('\\"', '"').replace('\\\\', '\\')
        try:
            arr, _ = decoder.raw_decode(candidate)
        except ValueError:
            continue
        if isinstance(arr, list):
            raw_points.extend(p for p in arr if isinstance(p, dict))

    return raw_points


def fetch_positions(token, session_id):
    """Load the LiveTrack share page for one session and parse its trackpoints.

    Returns a list of {lat, lng, recorded_at, ...} dicts ordered as Garmin
    renders them (chronological). Raises a descriptive Exception on any
    HTTP/parse failure so the caller can fail soft per-rider.
    """
    if not token or not session_id:
        raise ValueError('fetch_positions requires both token and session_id')

    url = _SHARE_PAGE_URL.format(session_id=session_id, token=token)
    try:
        resp = http_requests.get(
            url, timeout=_REQUEST_TIMEOUT,
            headers=_BROWSER_HEADERS,
        )
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

    raw_points = _extract_trackpoints_html(resp.text or '')

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
