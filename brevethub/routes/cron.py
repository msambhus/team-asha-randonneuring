"""BrevetHub scheduled-refresh endpoints (Vercel cron).

The calendar cache (rp_brevet_event) is warmed OFF the request path here so
/calendar only ever READS a warm cache and never blocks on the heavy ~672-event
national-feed scrape (which risks a cold serverless timeout). A daily Vercel cron
hits /cron/refresh-calendar; the endpoint scrapes via the shared, club-agnostic
scraper and upserts each event. It degrades gracefully — a scrape failure returns a
non-500 JSON body and leaves the last-good cache intact (an empty/failed scrape
never upserts), so a bad refresh can never wipe the calendar.

Route contract (pinned — see the frame plan): the production URL is exactly
``/cron/refresh-calendar`` (a SINGLE ``/cron`` segment). The blueprint owns the
``/cron`` prefix (registered in brevethub/app.py) and the decorator is LEAF-ONLY
(``/refresh-calendar``); putting ``/cron`` in both would make Flask serve
``/cron/cron/refresh-calendar`` and the Vercel-scheduled request would 404, silently
leaving the cache cold. GET and POST are both accepted because Vercel cron issues a
GET.

Isolation: imports only flask / stdlib / brevethub.* / shared.*, and the scrape+upsert
helper it reuses (brevethub.routes.calendar._scrape_and_upsert) touches only the
rp_brevet_event table, so test_brevethub_isolation.py and test_rp_only.py stay green.
"""
import hmac

from flask import Blueprint, current_app, jsonify, request

from brevethub import models
from brevethub.routes.calendar import _scrape_and_upsert
from shared.weather import (FORECAST_HORIZON_DAYS, fetch_point_forecast,
                            resolve_region_coordinates)

cron_bp = Blueprint('cron', __name__)


def _verify_cron_auth():
    """Verify the ``Authorization: Bearer <CRON_SECRET>`` header.

    Returns an error ``(response, status)`` tuple to short-circuit on, or ``None``
    when the request is authorized. Uses ``hmac.compare_digest`` so the comparison is
    constant-time and cannot be used to enumerate the secret. A missing CRON_SECRET
    config is a 500 (misconfiguration), a missing/wrong header is a 401.
    """
    expected_secret = current_app.config.get('CRON_SECRET')
    if not expected_secret:
        return jsonify({'error': 'CRON_SECRET not configured'}), 500

    auth_header = request.headers.get('Authorization', '')
    expected = f'Bearer {expected_secret}'
    if not auth_header or not hmac.compare_digest(auth_header, expected):
        current_app.logger.warning('Unauthorized cron request from %s', request.remote_addr)
        return jsonify({'error': 'Unauthorized'}), 401

    return None


@cron_bp.route('/refresh-calendar', methods=['GET', 'POST'])
def refresh_calendar():
    """Scrape the RUSA national calendar and upsert it into the rp_brevet_event cache.

    Auth-gated (Bearer CRON_SECRET). On success returns ``{"refreshed": N}`` where N
    is the number of events upserted (0 when the scrape returned nothing). A scrape or
    DB failure is logged and returned as a non-500 JSON body so a flaky refresh never
    pages the maintainer or clobbers the cache — the last-good calendar keeps serving.
    Idempotent: upsert_brevet_event is an ON CONFLICT upsert keyed on
    (date, name, distance_km), so re-running simply refreshes rows in place.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    try:
        refreshed = _scrape_and_upsert()
    except Exception as e:
        current_app.logger.warning('RUSA calendar refresh failed: %s', e)
        return jsonify({'ok': False, 'error': 'refresh failed', 'refreshed': 0}), 200

    current_app.logger.info('RUSA calendar refresh upserted %s events', refreshed)
    return jsonify({'ok': True, 'refreshed': refreshed}), 200


@cron_bp.route('/fetch-brevet-weather', methods=['GET', 'POST'])
def fetch_brevet_weather():
    """Fetch Open-Meteo point forecasts for near-term brevets into the rp_* cache.

    Off the request path (the TA-237 lesson: never fetch weather on a page load).
    Auth-gated (Bearer CRON_SECRET). Loads near-term upcoming events with a region
    (get_weather_forecast_targets), resolves each region to an approximate start
    coordinate, fetches a keyless Open-Meteo daily forecast for the brevet date, and
    upserts the raw JSON into rp_brevet_weather. The calendar READS this cache only.

    Fails SOFT per event: an event whose region can't be resolved is skipped (no
    coordinate → nothing honest to fetch), and a transient Open-Meteo error for one
    event is logged and counted as a failure without 500ing the cron or clobbering
    that event's last-good cache row (the upsert only runs on a successful fetch).
    Idempotent — the upsert is ON CONFLICT (event_id, forecast_date), so re-running
    simply refreshes rows in place. Returns
    ``{ok, fetched, skipped, failed, considered}`` for observability.

    Route contract (pinned, same as refresh-calendar): the production URL is exactly
    ``/cron/fetch-brevet-weather`` — the blueprint owns the ``/cron`` prefix and this
    decorator is LEAF-ONLY, so the composed URL is single-prefixed and the
    Vercel-scheduled GET reaches the handler (a double ``/cron`` prefix would 404).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    try:
        targets = models.get_weather_forecast_targets(horizon_days=FORECAST_HORIZON_DAYS)
    except Exception as e:
        current_app.logger.warning('Brevet weather target load failed: %s', e)
        return jsonify({'ok': False, 'error': 'target load failed',
                        'fetched': 0, 'skipped': 0, 'failed': 0}), 200

    fetched = skipped = failed = 0
    for event in targets:
        coords = resolve_region_coordinates(event.get('region'))
        if not coords:
            skipped += 1
            continue
        try:
            weather_data = fetch_point_forecast(coords[0], coords[1], event['date'])
            if not weather_data:
                # Outside the forecast horizon (or empty) — nothing honest to store.
                skipped += 1
                continue
            models.upsert_brevet_weather(event['id'], event['date'], weather_data)
            fetched += 1
        except Exception as e:
            # Fail soft: keep the last-good cache row for this event, keep going.
            current_app.logger.warning(
                'Brevet weather fetch failed for event %s (%s): %s',
                event.get('id'), event.get('region'), e)
            failed += 1

    current_app.logger.info(
        'Brevet weather cron: fetched=%s skipped=%s failed=%s of %s considered',
        fetched, skipped, failed, len(targets))
    return jsonify({'ok': True, 'fetched': fetched, 'skipped': skipped,
                    'failed': failed, 'considered': len(targets)}), 200
