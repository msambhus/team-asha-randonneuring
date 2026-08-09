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
import time
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

from brevethub import models
from brevethub.routes.calendar import _scrape_and_upsert
from brevethub.routes.strava import compute_and_cache_eddington
from brevethub.shared.garmin_livetrack import fetch_positions, parse_session
from brevethub.shared.rwgps import (build_ride_plan, extract_controls,
                                    extract_rwgps_route_id, fetch_route)
from shared.live_radial import track_from_route
from shared.rusa import fetch_rider_results
from shared.rusa_calendar import get_rwgps_url_from_route
from shared.weather import (FORECAST_HORIZON_DAYS, fetch_point_forecast,
                            fetch_route_weather, resolve_region_coordinates,
                            sample_track_points)

# Downsample cap for the cached elevation track — plenty for the ~1000px-wide rpv2
# gradient profile SVG while keeping the JSONB row lean.
ROUTE_WEATHER_ELEVATION_TRACK_POINTS = 800

# Re-warm window for the route-keyed elevation cache. Route geometry is near-static, so a
# generous freshness window keeps the warm-plan-elevation cron cheap; ?force=1 bypasses it.
ELEVATION_CACHE_FRESH_DAYS = 30

cron_bp = Blueprint('cron', __name__)

# RUSA finish-time matching window — mirrors the parent web app sync tolerance: an
# official result matches a finished sign-up when the dates are within +-10 days AND
# the distances within +-20 km (or both are >= 1000 km, where RUSA rounds distances).
RUSA_MATCH_DATE_DAYS = 10
RUSA_MATCH_DISTANCE_KM = 20
RUSA_LONG_BREVET_KM = 1000

# Dense (15 km) route sampling for along-route weather — matches Team Asha's
# fetch-route-weather cron so the two engines sample identically.
ROUTE_WEATHER_SAMPLE_INTERVAL_M = 15000

# A cached route-weather row fetched within this many hours is considered fresh, so a
# same-day re-run of the warm cron skips it (idempotent) instead of re-hitting
# Open-Meteo. The daily cadence means the row still refreshes every day.
ROUTE_WEATHER_FRESH_HOURS = 12

# Keep every Nth RWGPS track point for the cached map polyline — a compact route line
# for the guest Mapbox weather tab (matches Team Asha's weather-map decimation).
ROUTE_WEATHER_POLYLINE_DECIMATION = 20


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


def _coerce_date(value):
    """Return a datetime.date out of a date-like value or an ISO string, else None.

    The cached rusa_cache stores ISO date strings; the live shared fetcher returns a
    datetime.date. Both are normalized here so the matcher can subtract them safely.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _match_rusa_result(event_date, distance_km, results):
    """Return the official finish time in RUSA results matching a finished sign-up.

    Matches by date within +-RUSA_MATCH_DATE_DAYS and distance within
    +-RUSA_MATCH_DISTANCE_KM (or both >= RUSA_LONG_BREVET_KM). Accepts BOTH the cached
    rusa_cache shape (ISO date strings) and the live fetcher shape (datetime.date),
    coercing each via _coerce_date. Returns None when nothing matches or the matched
    finish time is blank. This matcher is BH-native (it reads BrevetHub cached shape),
    so it is not promoted to shared/ — only the framework-free RUSA fetcher is shared.
    """
    target = _coerce_date(event_date)
    if target is None:
        return None
    try:
        distance_km = int(distance_km or 0)
    except (TypeError, ValueError):
        distance_km = 0
    for r in results or []:
        r_date = _coerce_date(r.get('date'))
        if r_date is None:
            continue
        try:
            r_dist = int(r.get('distance_km') or 0)
        except (TypeError, ValueError):
            continue
        date_diff = abs((target - r_date).days)
        dist_diff = abs(distance_km - r_dist)
        if date_diff <= RUSA_MATCH_DATE_DAYS and (
                dist_diff <= RUSA_MATCH_DISTANCE_KM
                or (distance_km >= RUSA_LONG_BREVET_KM and r_dist >= RUSA_LONG_BREVET_KM)):
            finish_time = (r.get('finish_time') or '').strip()
            if finish_time:
                return {'finish_time': finish_time,
                        'homologation_number': str(r.get('homologation_number') or '').strip() or None}
    return None


def _match_rusa_finish_time(event_date, distance_km, results):
    """Backward-compatible helper returning only the official finish time."""
    result = _match_rusa_result(event_date, distance_km, results)
    return result['finish_time'] if result else None


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

    return jsonify(run_refresh_calendar()), 200


def run_refresh_calendar():
    """Run the calendar refresh core for cron and the operator console."""
    try:
        refreshed = _scrape_and_upsert()
    except Exception as e:
        current_app.logger.warning('RUSA calendar refresh failed: %s', e)
        return {'ok': False, 'error': 'refresh failed', 'refreshed': 0}
    current_app.logger.info('RUSA calendar refresh upserted %s events', refreshed)
    return {'ok': True, 'refreshed': refreshed}


@cron_bp.route('/finalize-signups', methods=['GET', 'POST'])
def finalize_signups():
    """Auto-finalize past-date going sign-ups to finished (keyless logic).

    Auth-gated (Bearer CRON_SECRET). Mirrors the parent web app auto-finalize:
    flips every past-date ``going`` rp_event_signup to ``finished`` and returns
    ``{"finalized": N}``. Tenant-agnostic — the promotion is keyed on the event date
    and the going status only (no club scoping), and it never touches an
    interested/maybe/withdraw row or a future-date row. A DB failure is logged and
    returned as a non-500 JSON body so a flaky run never pages the maintainer.

    Scheduling: this runs BEFORE /cron/sync-rusa-results in the daily Vercel cron so
    the RUSA sync has freshly finished rows to back-fill (see brevethub/vercel.json).

    Route contract (pinned, same as the other crons): the production URL is exactly
    ``/cron/finalize-signups`` — the blueprint owns the ``/cron`` prefix and this
    decorator is LEAF-ONLY, so a double ``/cron`` prefix cannot 404 the
    Vercel-scheduled request. GET and POST are both accepted (Vercel cron issues GET).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    try:
        finalized = models.auto_finalize_past_signups()
    except Exception as e:
        current_app.logger.warning('Sign-up auto-finalize failed: %s', e)
        return jsonify({'ok': False, 'error': 'finalize failed', 'finalized': 0}), 200

    current_app.logger.info('Sign-up auto-finalize: finalized=%s', finalized)
    return jsonify({'ok': True, 'finalized': finalized}), 200


@cron_bp.route('/sync-rusa-results', methods=['GET', 'POST'])
def sync_rusa_results():
    """Back-fill official RUSA finish times onto finished sign-ups (the sole real
    finish_time writer).

    Auth-gated (Bearer CRON_SECRET). For every finished rp_event_signup whose rider
    has a rusa_id and whose finish_time is still empty, it matches the rider RUSA
    results to the event by date (+-10 days) and distance (+-20 km, or both >= 1000
    km) and writes the official finish time — mirroring the parent web app
    sync_rusa_finish_times. It PREFERS the RUSA history BrevetHub already caches
    (rp_rider.rusa_cache) and only falls back to a live shared fetch when the cache is
    empty, memoized once per rusa_id so a batch never re-fetches. No page load ever
    scrapes. Runs AFTER /cron/finalize-signups so freshly finished rows are covered
    the same day (see brevethub/vercel.json).

    Fails SOFT: a target-load failure returns a non-500 JSON body, and a live-fetch
    failure for one rider is logged and counted without 500-ing the run or clobbering
    a good finish time. Returns ``{ok, synced, considered}`` for observability.

    Route contract (pinned, same as the other crons): the production URL is exactly
    ``/cron/sync-rusa-results`` — the blueprint owns the ``/cron`` prefix and this
    decorator is LEAF-ONLY, so a double ``/cron`` prefix cannot 404 the
    Vercel-scheduled request. GET and POST are both accepted (Vercel cron issues GET).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    return jsonify(run_sync_rusa_results()), 200


def run_sync_rusa_results():
    """Run the official-results sync core for cron and the operator console."""
    try:
        targets = models.get_signups_needing_finish_time()
    except Exception as e:
        current_app.logger.warning('RUSA sync target load failed: %s', e)
        return {'ok': False, 'error': 'target load failed',
                'synced': 0, 'considered': 0}

    live_by_rusa = {}
    synced = 0
    for row in targets:
        cached = row.get('rusa_cache')
        if cached:
            results = cached
            cached_match = _match_rusa_result(row.get('date'), row.get('distance_km'), cached)
            # Older cached history has finish times but not certificate numbers.
            # Refresh that rider once so the official Cert No. can be retained.
            if not cached_match or not cached_match.get('homologation_number'):
                if row.get('rusa_id') in live_by_rusa:
                    results = live_by_rusa[row.get('rusa_id')]
                else:
                    try:
                        live_by_rusa[row.get('rusa_id')] = fetch_rider_results(row.get('rusa_id'))
                        results = live_by_rusa[row.get('rusa_id')]
                    except Exception as e:
                        current_app.logger.warning('RUSA certificate fetch failed for rider %s: %s', row.get('rider_id'), e)
        else:
            rusa_id = row.get('rusa_id')
            if rusa_id in live_by_rusa:
                results = live_by_rusa[rusa_id]
            else:
                try:
                    results = fetch_rider_results(rusa_id)
                except Exception as e:
                    current_app.logger.warning(
                        'RUSA fetch failed for rider %s: %s', row.get('rider_id'), e)
                    results = []
                live_by_rusa[rusa_id] = results
        matched = _match_rusa_result(
            row.get('date'), row.get('distance_km'), results)
        if matched and models.set_signup_finish_time(row['id'], matched['finish_time'], matched.get('homologation_number')):
            synced += 1

    current_app.logger.info(
        'RUSA finish-time sync: synced=%s of %s considered', synced, len(targets))
    return {'ok': True, 'synced': synced, 'considered': len(targets)}


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

    return jsonify(run_fetch_brevet_weather()), 200


def run_fetch_brevet_weather():
    """Run the point-forecast warmer for cron and the operator console."""
    try:
        targets = models.get_weather_forecast_targets(horizon_days=FORECAST_HORIZON_DAYS)
    except Exception as e:
        current_app.logger.warning('Brevet weather target load failed: %s', e)
        return {'ok': False, 'error': 'target load failed',
                'fetched': 0, 'skipped': 0, 'failed': 0}

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
    return {'ok': True, 'fetched': fetched, 'skipped': skipped,
            'failed': failed, 'considered': len(targets)}


# Max RUSA route-page scrapes attempted per backfill run. Bounded so one run makes at
# most BATCH_SIZE fetches (not the whole ~669-event backlog), keeping it well inside
# the serverless budget; successive daily runs chip away at the remaining NULL
# rwgps_url rows until they converge on the unresolvable-route floor. A mid-batch kill
# is safe because the next run resumes on whatever NULLs are left.
BATCH_SIZE = 25


@cron_bp.route('/backfill-rwgps-urls', methods=['GET', 'POST'])
def backfill_rwgps_urls():
    """Backfill the NULL rwgps_url column on rp_brevet_event, OFF the request path.

    Auth-gated (Bearer CRON_SECRET). The /calendar seed scrapes with
    ``fetch_rwgps=False`` because following ~669 route pages on a page load would blow
    the serverless timeout, so brevet rows land with a rusa_route_id but no rwgps_url
    and /cron/warm-brevet-plans has nothing to warm. This cron closes that gap: it
    loads a bounded batch of URL-less events that carry a rusa_route_id (upcoming
    first) and, for each, scrapes the RUSA route-detail page via the shared
    get_rwgps_url_from_route helper and writes back ONLY the rwgps_url column.

    Bounded + idempotent + fail-soft: at most BATCH_SIZE fetches per run; the reader
    selects only rows whose rwgps_url IS NULL and the writer re-asserts that guard, so
    a filled row is never reselected or overwritten and a re-run yields stable counts.
    A fetch that raises or returns None leaves that row NULL (a route page with no
    RideWithGPS link is simply re-tried next run, cheaply) and the batch keeps going.
    Runs daily BEFORE /cron/warm-brevet-plans so a URL filled in the morning is warmed
    into a ride plan the same day (see brevethub/vercel.json). Returns
    ``{ok, considered, filled, still_null, remaining}`` for observability — remaining
    is the still-NULL backlog after this run (up to one batch; it saturates while more
    than a batch is left) and trending it toward the unresolvable-route floor proves
    convergence.

    Route contract (pinned, same as the other crons): the production URL is exactly
    ``/cron/backfill-rwgps-urls`` — the blueprint owns the ``/cron`` prefix and this
    decorator is LEAF-ONLY, so the composed URL is single-prefixed and the
    Vercel-scheduled GET reaches the handler (a double ``/cron`` prefix would 404).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    return jsonify(run_backfill_rwgps_urls()), 200


def run_backfill_rwgps_urls():
    """Run one bounded RWGPS discovery batch for cron and the operator console."""
    try:
        targets = models.get_events_needing_rwgps_url(BATCH_SIZE)
    except Exception as e:
        current_app.logger.warning('RWGPS backfill target load failed: %s', e)
        return {'ok': False, 'error': 'target load failed',
                'considered': 0, 'filled': 0, 'still_null': 0,
                'remaining': 0}

    filled = 0
    for event in targets:
        route_id = event.get('rusa_route_id')
        try:
            rwgps_url = get_rwgps_url_from_route(route_id)
            if rwgps_url and models.set_event_rwgps_url(event['id'], rwgps_url):
                filled += 1
            # A None result (route page has no RideWithGPS link) leaves the row NULL.
        except Exception as e:
            # Fail soft: one route-page scrape error never aborts the batch.
            current_app.logger.warning(
                'RWGPS backfill failed for event %s (route %s): %s',
                event.get('id'), route_id, e)

    considered = len(targets)
    still_null = considered - filled
    try:
        remaining = len(models.get_events_needing_rwgps_url(BATCH_SIZE))
    except Exception:
        remaining = still_null

    current_app.logger.info(
        'RWGPS backfill: filled=%s of %s considered, ~%s still needing a URL',
        filled, considered, remaining)
    return {'ok': True, 'considered': considered, 'filled': filled,
            'still_null': still_null, 'remaining': remaining}


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

    return jsonify(run_warm_brevet_plans()), 200


def run_warm_brevet_plans():
    """Run the route-plan warmer core for cron and the operator console."""
    try:
        targets = models.get_route_plan_warm_targets()
    except Exception as e:
        current_app.logger.warning('Route-plan warm target load failed: %s', e)
        return {'ok': False, 'error': 'target load failed',
                'warmed': 0, 'skipped': 0, 'failed': 0}

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
            # Warm BOTH variants per event: conservative + aggressive, each with
            # clock-typed meal breaks (start_time drives the clock). Fetch the route
            # once; the two builds differ only in their pacing profile. Counts stay
            # PER EVENT (not per variant), so the {warmed,skipped,failed} shape is
            # unchanged and re-runs are idempotent.
            plan_id = None
            for variant in ('conservative', 'aggressive'):
                built = build_ride_plan(
                    route_data, controls, profile=variant, insert_meals=True,
                    start_time=event.get('start_time'))
                plan_id = models.upsert_brevet_route_plan(
                    event['id'], built['plan'], built['stops'], variant=variant)
                if plan_id is None:
                    # A club owner manages this brevet's plan — the guard blocks both
                    # variants identically, so stop before the second write.
                    break
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
    return {'ok': True, 'warmed': warmed, 'skipped': skipped,
            'failed': failed, 'considered': len(targets)}


def _decimate_track_polyline(track_points):
    """Reduce a dense RWGPS track to a compact ``[[lat, lng], ...]`` map line.

    Keeps every ``ROUTE_WEATHER_POLYLINE_DECIMATION``-th point plus the final one, so
    the cached polyline stays small but the route still closes. RWGPS track points use
    ``y`` for latitude and ``x`` for longitude; points missing either are skipped.
    Returns None for an empty track so the caller stores SQL NULL (read path then falls
    back to the sample points). Matches Team Asha's weather-map decimation.
    """
    if not track_points:
        return None
    polyline = []
    for i, pt in enumerate(track_points):
        if i % ROUTE_WEATHER_POLYLINE_DECIMATION != 0:
            continue
        lat, lng = pt.get('y'), pt.get('x')
        if lat is not None and lng is not None:
            polyline.append([lat, lng])
    last = track_points[-1]
    if last.get('y') is not None and last.get('x') is not None:
        tail = [last['y'], last['x']]
        if not polyline or polyline[-1] != tail:
            polyline.append(tail)
    return polyline or None


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

    return jsonify(run_warm_brevet_route_weather()), 200


def run_warm_brevet_route_weather():
    """Run the route-weather warmer for cron and the operator console."""
    try:
        targets = models.get_route_weather_warm_targets(horizon_days=FORECAST_HORIZON_DAYS)
    except Exception as e:
        current_app.logger.warning('Route-weather warm target load failed: %s', e)
        return {'ok': False, 'error': 'target load failed',
                'warmed': 0, 'skipped': 0, 'failed': 0}

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

            # Decimate the RWGPS track into a compact [[lat, lng], ...] line for the
            # guest Mapbox weather tab, drawn straight from cache (no live fetch on the
            # guest page). None when the route has no usable points → read-path falls
            # back to the coarser sample_points line.
            polyline = _decimate_track_polyline(track_points)

            # Downsampled elevation track ([{lat, lng, dist_m, e_m}, ...]) for the rpv2
            # gradient elevation profile — built from the SAME route_data already
            # fetched here, so the guest /plan render reads it from cache instead of
            # calling RWGPS live. Empty [] when the route has no usable points →
            # build_elevation_profile renders an empty profile.
            elevation_track = track_from_route(
                route_data, max_points=ROUTE_WEATHER_ELEVATION_TRACK_POINTS) or None

            models.upsert_brevet_route_weather(
                event['id'], forecast_date, weather_data, samples, polyline,
                elevation_track)
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
    return {'ok': True, 'warmed': warmed, 'skipped': skipped,
            'failed': failed, 'considered': len(targets)}


@cron_bp.route('/warm-plan-elevation', methods=['GET', 'POST'])
def warm_plan_elevation():
    """Cache the RWGPS elevation track for EVERY route referenced by an rp_brevet_route_plan,
    so the guest rpv2 /plan gradient profile renders for ANY plan (past or upcoming) with no
    live RWGPS fetch on the request path. Unlike warm-brevet-route-weather (upcoming events
    only), this warms all plan routes into the route-keyed rp_route_geometry_cache.

    Auth-gated (Bearer CRON_SECRET). Idempotent: a route warmed within
    ELEVATION_CACHE_FRESH_DAYS is skipped unless a truthy ?force is passed. Fail-soft per
    route: an RWGPS error keeps the last-good row and is counted, never 500s the cron.
    Returns {ok, warmed, skipped, failed, considered}.

    Route contract: leaf-only decorator; the blueprint owns the /cron prefix, so the
    production URL is exactly /cron/warm-plan-elevation (a double /cron would 404).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    force = request.args.get('force') in ('1', 'true', 'yes')
    return jsonify(run_warm_plan_elevation(force=force)), 200


def run_warm_plan_elevation(force=False):
    """Warm route elevation geometry for cron and the operator console."""
    from datetime import datetime, timezone, timedelta

    api_key = current_app.config.get('RWGPS_API_KEY')
    auth_token = current_app.config.get('RWGPS_AUTH_TOKEN')

    try:
        plans = models.get_brevet_route_plan_route_ids()
    except Exception as e:
        current_app.logger.warning('warm-plan-elevation: plan load failed: %s', e)
        return {'ok': False, 'error': 'plan load failed',
                'warmed': 0, 'skipped': 0, 'failed': 0}

    route_ids = set()
    for p in plans:
        rid = p.get('rwgps_route_id') or extract_rwgps_route_id(p.get('rwgps_url'))
        if rid:
            route_ids.add(str(rid))

    warmed = skipped = failed = 0
    for route_id in sorted(route_ids):
        try:
            if not force:
                fetched_at = models.get_rp_route_geometry_freshness(route_id)
                if fetched_at and (datetime.now(timezone.utc) - fetched_at
                                   < timedelta(days=ELEVATION_CACHE_FRESH_DAYS)):
                    skipped += 1
                    continue
            route_data = fetch_route(route_id, api_key, auth_token)
            elevation_track = track_from_route(
                route_data, max_points=ROUTE_WEATHER_ELEVATION_TRACK_POINTS) or None
            models.upsert_rp_route_geometry(route_id, elevation_track)
            warmed += 1
        except Exception as e:
            failed += 1
            current_app.logger.warning(
                'warm-plan-elevation: route %s failed (last-good kept): %s', route_id, e)

    current_app.logger.info(
        'warm-plan-elevation: warmed=%s skipped=%s failed=%s of %s routes',
        warmed, skipped, failed, len(route_ids))
    return {'ok': True, 'warmed': warmed, 'skipped': skipped,
            'failed': failed, 'considered': len(route_ids)}


# Short pause between riders in the Eddington refresh so a full-history fetch for
# many riders does not burst the Strava rate limit. Kept small so the daily cron
# still finishes well inside the serverless budget.
EDDINGTON_REFRESH_SLEEP_SECONDS = 1


@cron_bp.route('/refresh-eddington', methods=['GET', 'POST'])
def refresh_eddington():
    """Recompute every connected rider cycling Eddington number, OFF the request path.

    Auth-gated (Bearer CRON_SECRET). Iterates every rp_strava_connection and, for
    each, recomputes E from that rider own full Strava history with their own token
    and caches it (compute_and_cache_eddington). Precomputing here keeps the PUBLIC
    rider profile a pure cache read: a public viewer holds no token for the viewed
    rider and must never fetch, so the number has to be ready before they look.

    Fails SOFT per rider: a Strava error or rate-limit on one rider is logged and
    counted as a failure without 500ing the cron or aborting the batch, and a short
    backoff between riders keeps the run under the Strava rate limit. Returns
    ``{ok, refreshed, failed, considered}`` for observability — a rising failed
    signals Strava rate-limit/token trouble.

    Route contract (pinned, same as the other crons): the production URL is exactly
    ``/cron/refresh-eddington`` — the blueprint owns the ``/cron`` prefix and this
    decorator is LEAF-ONLY, so a double ``/cron`` prefix cannot 404 the
    Vercel-scheduled request. GET and POST are both accepted (Vercel cron issues GET).
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    return jsonify(run_refresh_eddington()), 200


def run_refresh_eddington():
    """Recompute connected-rider Eddington values for cron and operators."""
    try:
        connections = models.get_strava_connections_for_eddington()
    except Exception as e:
        current_app.logger.warning('Eddington refresh: connection load failed: %s', e)
        return {'ok': False, 'error': 'connection load failed',
                'refreshed': 0, 'failed': 0, 'considered': 0}

    refreshed = failed = 0
    for idx, connection in enumerate(connections):
        rider_id = connection.get('rider_id')
        try:
            compute_and_cache_eddington(rider_id, connection)
            refreshed += 1
        except Exception as e:
            # Fail soft: one rider Strava/token error never aborts the batch.
            current_app.logger.warning(
                'Eddington refresh failed for rider %s: %s', rider_id, e)
            failed += 1
        # Backoff between riders (not after the last one) to respect the rate limit.
        if idx < len(connections) - 1:
            time.sleep(EDDINGTON_REFRESH_SLEEP_SECONDS)

    current_app.logger.info(
        'Eddington refresh: refreshed=%s failed=%s of %s considered',
        refreshed, failed, len(connections))
    return {'ok': True, 'refreshed': refreshed, 'failed': failed,
            'considered': len(connections)}


# Retention + downsample tuning (mirrors Team Asha's poll_garmin_livetrack).
LIVE_RETENTION_DAYS = 7
LIVE_MIN_GAP_SECONDS = 30   # keep at most one stored point per 30s per rider


@cron_bp.route('/poll-garmin-livetrack', methods=['GET', 'POST'])
def poll_garmin_livetrack():
    """Poll Garmin LiveTrack for opted-in riders, store positions, purge old data.

    Mirrors Team Asha's poll_garmin_livetrack but on the rp_ tables and the vendored
    shared engine. Auth-gated (Bearer CRON_SECRET). For each rider opted in with a
    Garmin session pointed at a ride, it re-derives the session id from the saved
    share URL, fetches the live trackpoints (all HTTP inside the shared engine),
    appends only points newer than the last stored one for THIS ride (idempotent),
    downsampled to at most one per LIVE_MIN_GAP_SECONDS, and tags each with the
    rider + ride so it shows only on that ride's member map. Fail-soft per rider:
    one bad/expired session never breaks the batch.

    Returns ``{ok, polled, inserted, skipped, failed}``:
      - polled:   riders we actually fetched for,
      - inserted: new position points stored,
      - skipped:  riders with no usable token/session or no active ride,
      - failed:   riders whose fetch raised (counted, then the batch continues).

    Scheduling / freshness caveat (see PR — this is the plan's anticipated Vercel
    fallback): the mission asked for ``* * * * *`` (every minute), but the BrevetHub
    Vercel project is on the Hobby plan, which caps cron jobs at ONCE PER DAY
    (±59 min) — a sub-daily expression fails the deployment outright. So per the
    plan's risk mitigation ("fall back to the tightest accepted schedule and state
    it"), brevethub/vercel.json schedules this at the tightest the plan allows
    (``0 10 * * *``), which is FAR coarser than Team Asha's ~3-min Railway loop and
    is not real-time. CONSEQUENCE (stated in the PR as a deploy prerequisite): with
    a daily poll, a rider who links their Garmin mid-ride is not ingested until the
    next run, so the member map can stay empty/stale during the ride — which is why
    the attach flow tells riders their position "appears after the next tracking
    poll", not "within minutes". Making this near-real-time is a deploy-time
    decision outside this change: upgrade the BrevetHub Vercel project to Pro (min
    interval once/minute) and set ``* * * * *`` here. The endpoint itself is
    scheduler-agnostic and unchanged either way.

    Route contract (pinned, same as the other crons): the production URL is exactly
    ``/cron/poll-garmin-livetrack`` — the blueprint owns the ``/cron`` prefix and
    this decorator is LEAF-ONLY, so a double ``/cron`` prefix can't 404 the
    Vercel-scheduled request. GET and POST are both accepted because Vercel cron
    issues a GET.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    try:
        tracked = models.get_enabled_live_tracking_rp()
    except Exception as e:
        current_app.logger.warning('poll-garmin-livetrack: rider load failed: %s', e)
        return jsonify({'ok': False, 'error': 'rider load failed',
                        'polled': 0, 'inserted': 0, 'skipped': 0, 'failed': 0}), 200

    polled = inserted = skipped = failed = 0
    for row in tracked:
        rider_id = row['rider_id']
        ride_id = row.get('active_ride_id')
        token = row.get('garmin_session_token')
        session_url = row.get('garmin_session_url')
        # Prefer the stored token; re-derive session_id from the saved share URL.
        parsed = parse_session(session_url) if session_url else None
        session_id = parsed['session_id'] if parsed else None
        if not token or not session_id or not ride_id:
            # Nothing to fetch or nowhere to attribute points — skip, don't fail.
            skipped += 1
            continue

        polled += 1
        try:
            points = fetch_positions(token, session_id)
        except Exception as e:
            # Fail soft: one rider's expired/blocked session never breaks the batch.
            current_app.logger.warning(
                'poll-garmin-livetrack: rider %s fetch failed: %s', rider_id, e)
            failed += 1
            continue

        # Append only points newer than the last stored one FOR THIS RIDE (so a
        # re-run inserts nothing new — idempotent), downsampled to at most one per
        # LIVE_MIN_GAP_SECONDS so we accumulate a real history, not just the latest.
        try:
            last_at = models.get_last_position_recorded_at_rp(rider_id, ride_id)
        except Exception:
            last_at = None
        fresh = sorted(
            (p for p in points if p.get('recorded_at') is not None
             and (last_at is None or p['recorded_at'] > last_at)),
            key=lambda p: p['recorded_at'],
        )
        kept_at = None
        for p in fresh:
            if kept_at is not None and \
                    (p['recorded_at'] - kept_at).total_seconds() < LIVE_MIN_GAP_SECONDS:
                continue
            if models.insert_live_position_rp(
                    rider_id=rider_id, lat=p['lat'], lng=p['lng'],
                    recorded_at=p['recorded_at'], source='garmin',
                    speed=p.get('speed'), heart_rate=p.get('heart_rate'),
                    power=p.get('power'), cadence=p.get('cadence'),
                    ride_id=ride_id):
                kept_at = p['recorded_at']
                inserted += 1

    try:
        purged = models.purge_old_positions_rp(LIVE_RETENTION_DAYS)
    except Exception as e:
        current_app.logger.warning('poll-garmin-livetrack: purge failed: %s', e)
        purged = None

    current_app.logger.info(
        'poll-garmin-livetrack: polled=%s inserted=%s skipped=%s failed=%s purged=%s',
        polled, inserted, skipped, failed, purged)
    return jsonify({'ok': True, 'polled': polled, 'inserted': inserted,
                    'skipped': skipped, 'failed': failed}), 200
