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
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

from brevethub import models
from brevethub.routes.calendar import _scrape_and_upsert
from brevethub.shared.rwgps import (build_ride_plan, extract_controls,
                                    extract_rwgps_route_id, fetch_route)
from shared.weather import (FORECAST_HORIZON_DAYS, fetch_point_forecast,
                            fetch_route_weather, resolve_region_coordinates,
                            sample_track_points)

cron_bp = Blueprint('cron', __name__)

# Dense (15 km) route sampling for along-route weather — matches Team Asha's
# fetch-route-weather cron so the two engines sample identically.
ROUTE_WEATHER_SAMPLE_INTERVAL_M = 15000

# A cached route-weather row fetched within this many hours is considered fresh, so a
# same-day re-run of the warm cron skips it (idempotent) instead of re-hitting
# Open-Meteo. The daily cadence means the row still refreshes every day.
ROUTE_WEATHER_FRESH_HOURS = 12


def _route_weather_is_fresh(fetched_at):
    """True when a cached route-weather row was fetched recently enough to reuse.

    ``fetched_at`` is the row's TIMESTAMPTZ (timezone-aware from Postgres). A missing
    value, or any comparison error, is treated as stale so the cron re-fetches rather
    than silently skipping.
    """
    if not fetched_at:
        return False
    try:
        now = datetime.now(fetched_at.tzinfo) if fetched_at.tzinfo else datetime.now()
        return (now - fetched_at) < timedelta(hours=ROUTE_WEATHER_FRESH_HOURS)
    except (TypeError, ValueError):
        return False


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


@cron_bp.route('/warm-brevet-plans', methods=['GET', 'POST'])
def warm_brevet_plans():
    """Pre-fetch + persist real RWGPS ride plans for upcoming brevets, OFF the
    request path (mirrors /cron/fetch-brevet-weather).

    Auth-gated (Bearer CRON_SECRET). Loads upcoming events that carry an rwgps_url
    (get_route_plan_warm_targets), and for each: extracts the RWGPS route id, fetches
    the route via the reused shared engine (credentials from the BrevetHub config —
    the guest /plan page NEVER calls RWGPS live), builds the plan, and upserts it
    into rp_brevet_route_plan[_stop]. The guest page then READS this cache only.

    Fails SOFT per event: an event with an unparseable rwgps_url is skipped; a
    transient RWGPS/build error for one event is logged and counted as a failure
    without 500ing the cron or clobbering that event's last-good plan (the upsert
    only runs on a successful build). Idempotent — the upsert is ON CONFLICT
    (event_id), so re-running refreshes rows in place. Returns
    ``{ok, warmed, skipped, failed, considered}`` for observability.

    Route contract (pinned, same as the other crons): the production URL is exactly
    ``/cron/warm-brevet-plans`` — the blueprint owns the ``/cron`` prefix and this
    decorator is LEAF-ONLY, so the composed URL is single-prefixed and the
    Vercel-scheduled GET reaches the handler (a double ``/cron`` prefix would 404).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    try:
        targets = models.get_route_plan_warm_targets()
    except Exception as e:
        current_app.logger.warning('Route-plan warm target load failed: %s', e)
        return jsonify({'ok': False, 'error': 'target load failed',
                        'warmed': 0, 'skipped': 0, 'failed': 0}), 200

    api_key = current_app.config.get('RWGPS_API_KEY')
    auth_token = current_app.config.get('RWGPS_AUTH_TOKEN')

    warmed = skipped = failed = 0
    for event in targets:
        route_id = extract_rwgps_route_id(event.get('rwgps_url'))
        if not route_id:
            # No parseable RWGPS route — nothing honest to build.
            skipped += 1
            continue
        try:
            route_data = fetch_route(route_id, api_key, auth_token)
            controls = extract_controls(route_data)
            built = build_ride_plan(route_data, controls)
            plan_id = models.upsert_brevet_route_plan(
                event['id'], built['plan'], built['stops'])
            if plan_id is None:
                # A club owner manages this brevet's plan — leave it, don't clobber.
                skipped += 1
            else:
                warmed += 1
        except Exception as e:
            # Fail soft: keep the last-good plan for this event, keep going.
            current_app.logger.warning(
                'Route-plan warm failed for event %s (%s): %s',
                event.get('id'), event.get('rwgps_url'), e)
            failed += 1

    current_app.logger.info(
        'Brevet route-plan cron: warmed=%s skipped=%s failed=%s of %s considered',
        warmed, skipped, failed, len(targets))
    return jsonify({'ok': True, 'warmed': warmed, 'skipped': skipped,
                    'failed': failed, 'considered': len(targets)}), 200


@cron_bp.route('/warm-brevet-route-weather', methods=['GET', 'POST'])
def warm_brevet_route_weather():
    """Pre-fetch + persist the dense along-route Open-Meteo forecast for near-term
    brevets, OFF the request path (mirrors /cron/warm-brevet-plans and Team Asha's
    fetch-route-weather cron).

    Auth-gated (Bearer CRON_SECRET). Loads upcoming events that have a PERSISTED real
    plan within Open-Meteo's 16-day forecast horizon (get_route_weather_warm_targets),
    and for each: resolves the RWGPS route id FROM THE PLAN (its rwgps_route_id, else
    its rwgps_url), fetches the route via the reused shared engine (credentials from the
    BrevetHub config — the guest /plan page NEVER calls RWGPS/Open-Meteo live), samples
    the track at 15 km, batch-fetches Open-Meteo, and upserts the forecast + sample
    points into rp_brevet_route_weather. The guest page then READS this cache only,
    mapping each stop to the nearest sample.

    The route comes from the persisted PLAN, not rp_brevet_event.rwgps_url: an admin can
    generate a plan from a different RWGPS URL than the event's (routes/admin.py), and
    the /plan page renders THAT plan's route — so the cached weather must be sampled
    from the same route the stops are mapped along, or the wind would be off the wrong
    course (or absent when only the plan has a URL).

    Idempotent two ways: a row already fetched within ROUTE_WEATHER_FRESH_HOURS is
    SKIPPED (no redundant Open-Meteo call on a same-day re-run), and the write itself
    is an ON CONFLICT (event_id, forecast_date) upsert. Fails SOFT per event: an event
    with an unparseable rwgps_url or an empty sample/forecast is skipped, and a
    transient RWGPS/Open-Meteo error for one event is logged and counted as a failure
    without 500ing the cron or clobbering that event's last-good row (the upsert only
    runs on a successful fetch). Returns ``{ok, warmed, skipped, failed, considered}``
    for observability.

    Route contract (pinned, same as the other crons): the production URL is exactly
    ``/cron/warm-brevet-route-weather`` — the blueprint owns the ``/cron`` prefix and
    this decorator is LEAF-ONLY, so the composed URL is single-prefixed and the
    Vercel-scheduled GET reaches the handler (a double ``/cron`` prefix would 404).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    try:
        targets = models.get_route_weather_warm_targets(horizon_days=FORECAST_HORIZON_DAYS)
    except Exception as e:
        current_app.logger.warning('Route-weather warm target load failed: %s', e)
        return jsonify({'ok': False, 'error': 'target load failed',
                        'warmed': 0, 'skipped': 0, 'failed': 0}), 200

    api_key = current_app.config.get('RWGPS_API_KEY')
    auth_token = current_app.config.get('RWGPS_AUTH_TOKEN')
    today = date.today()

    warmed = skipped = failed = 0
    for event in targets:
        # Prefer the persisted plan's route id; fall back to parsing the plan/event URL.
        route_id = event.get('rwgps_route_id') or extract_rwgps_route_id(event.get('rwgps_url'))
        if not route_id:
            # No parseable RWGPS route — nothing honest to fetch.
            skipped += 1
            continue
        forecast_date = event.get('date')
        try:
            # Idempotent skip: a fresh row already covers this event+date.
            existing = models.get_brevet_route_weather(event['id'], forecast_date)
            if existing and _route_weather_is_fresh(existing.get('fetched_at')):
                skipped += 1
                continue

            route_data = fetch_route(route_id, api_key, auth_token)
            track_points = (route_data or {}).get('track_points') or []
            samples = sample_track_points(
                track_points, interval_m=ROUTE_WEATHER_SAMPLE_INTERVAL_M)
            if not samples:
                # No usable geometry — nothing to sample.
                skipped += 1
                continue

            # Request enough days to REACH the ride date plus a buffer for multi-day
            # brevets, never fewer than Open-Meteo's 7-day default, capped at the
            # 16-day horizon (matches Team Asha's fetch-route-weather cron).
            days_out = (forecast_date - today).days if forecast_date else 0
            forecast_days = min(FORECAST_HORIZON_DAYS, max(7, days_out + 3))
            weather_data = fetch_route_weather(samples, forecast_days=forecast_days)
            if not weather_data:
                skipped += 1
                continue

            models.upsert_brevet_route_weather(
                event['id'], forecast_date, weather_data, samples)
            warmed += 1
        except Exception as e:
            # Fail soft: keep the last-good row for this event, keep going.
            current_app.logger.warning(
                'Route-weather warm failed for event %s (%s): %s',
                event.get('id'), event.get('rwgps_url'), e)
            failed += 1

    current_app.logger.info(
        'Brevet route-weather cron: warmed=%s skipped=%s failed=%s of %s considered',
        warmed, skipped, failed, len(targets))
    return jsonify({'ok': True, 'warmed': warmed, 'skipped': skipped,
                    'failed': failed, 'considered': len(targets)}), 200
