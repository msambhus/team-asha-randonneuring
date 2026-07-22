"""Cron endpoints for scheduled background tasks."""
import hmac
import os
import time
from datetime import date, datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, current_app

cron_bp = Blueprint('cron', __name__)


def _verify_cron_auth():
    """Verify CRON_SECRET authentication. Returns error response or None.

    Uses hmac.compare_digest to prevent timing-attack enumeration of the secret.
    """
    auth_header = request.headers.get('Authorization', '')
    expected_secret = current_app.config.get('CRON_SECRET')

    if not expected_secret:
        return jsonify({'error': 'CRON_SECRET not configured'}), 500

    expected = f'Bearer {expected_secret}'
    if not auth_header or not hmac.compare_digest(auth_header, expected):
        current_app.logger.warning('Unauthorized cron request from %s', request.remote_addr)
        return jsonify({'error': 'Unauthorized'}), 401

    return None


@cron_bp.route('/sync-strava', methods=['POST'])
def sync_strava():
    """Periodic Strava sync endpoint (called by GitHub Actions).

    Syncs recent Strava activities for all riders, then does gradual
    historical backfill (one rider per run, 90 days further back each time).

    Returns:
        JSON with sync results including per-rider details
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    # Get all riders with active Strava connection
    from models import get_all_active_strava_connections

    try:
        connections = get_all_active_strava_connections()
        current_app.logger.info(f'Found {len(connections)} active Strava connections to sync')
    except Exception as e:
        current_app.logger.error(f'Failed to fetch active connections: {e}')
        return jsonify({'error': 'Database error', 'detail': str(e)}), 500

    from services.strava import sync_rider_activities

    results = {
        'synced': 0,
        'failed': 0,
        'skipped': 0,
        'errors': [],
        'details': [],
        'backfill': None,
    }

    MAX_RIDERS_PER_RUN = 50

    connections_to_sync = connections[:MAX_RIDERS_PER_RUN]
    if len(connections) > MAX_RIDERS_PER_RUN:
        results['skipped'] = len(connections) - MAX_RIDERS_PER_RUN

    current_app.logger.info(
        f'Syncing {len(connections_to_sync)} riders '
        f'(skipping {results["skipped"]} due to batch limit)'
    )

    # --- Phase 1: Recent sync (last 7 days) for all riders ---
    for i, conn in enumerate(connections_to_sync):
        rider_id = conn['rider_id']
        rider_name = conn.get('rider_name', f'Rider {rider_id}')

        try:
            counts = sync_rider_activities(
                rider_id=rider_id,
                days=7,
                calculate_eddington=True,
            )

            results['synced'] += 1
            results['details'].append({
                'rider_id': rider_id,
                'name': rider_name,
                'new': counts['new'],
                'updated': counts['updated'],
            })
            current_app.logger.info(
                f'Synced {rider_name} (id={rider_id}): '
                f'{counts["new"]} new, {counts["updated"]} updated'
            )

            if i < len(connections_to_sync) - 1:
                time.sleep(1)

        except Exception as e:
            try:
                from models import get_db
                get_db().rollback()
            except Exception:
                pass
            results['failed'] += 1
            error_msg = f'{rider_name} (id={rider_id}): {str(e)}'
            results['errors'].append(error_msg)
            current_app.logger.error(f'Failed to sync {rider_name} (id={rider_id}): {e}')

            if '429' in str(e) or 'rate limit' in str(e).lower():
                current_app.logger.error('Hit Strava rate limit - stopping batch')
                results['skipped'] += len(connections_to_sync) - i - 1
                break

    # --- Phase 2: Gradual backfill (loop until rate-limited) ---
    # Optional: pass {"rider_id": 6} in request body to force backfill a specific rider
    force_rider_id = (request.get_json(silent=True) or {}).get('rider_id')
    backfill_rounds = []
    try:
        while True:
            backfill_result = _do_gradual_backfill(connections_to_sync, force_rider_id=force_rider_id)
            backfill_rounds.append(backfill_result)

            # Stop if all riders fully backfilled
            if backfill_result.get('status') == 'All riders fully backfilled':
                break

            # Stop on error (including rate limits)
            if backfill_result.get('error'):
                break

            time.sleep(1)
    except Exception as e:
        current_app.logger.error(f'Backfill loop stopped: {e}')
        backfill_rounds.append({'error': str(e)})

    results['backfill'] = backfill_rounds
    results['backfill_rounds'] = len(backfill_rounds)

    current_app.logger.info(
        f'Sync complete: {results["synced"]} synced, '
        f'{results["failed"]} failed, {results["skipped"]} skipped'
    )

    return jsonify(results), 200


def _do_gradual_backfill(connections, force_rider_id=None):
    """Backfill one rider per run, going 90 days further back each time.

    Uses backfill_cursor (stored in strava_connection) to track how far back
    we've searched. This avoids getting stuck when there are gaps in riding
    history (no activities for 90+ days).

    Args:
        connections: list of active strava connections
        force_rider_id: optional rider_id to backfill instead of auto-picking

    Returns:
        dict with backfill details
    """
    from models import (get_backfill_cursor, update_backfill_cursor,
                        get_oldest_activity_date)
    from services.strava import sync_rider_activities

    # Strava was founded in 2009; don't go back further than 2008
    EARLIEST_YEAR = 2008
    BACKFILL_DAYS = 90

    # If a specific rider is requested, use that one
    if force_rider_id:
        force_rider_id = int(force_rider_id)
        best_rider = next((c for c in connections if c['rider_id'] == force_rider_id), None)
        if not best_rider:
            return {'error': f'Rider {force_rider_id} not found in active connections'}
        cursor = get_backfill_cursor(force_rider_id)
        rider_name = best_rider.get('rider_name', f'Rider {force_rider_id}')
        current_app.logger.info(f'Backfill: forced for {rider_name} (id={force_rider_id}), cursor={cursor}')
    else:
        # Find rider with least history (most recent backfill cursor)
        best_rider = None
        cursor = None

        for conn in connections:
            rid = conn['rider_id']
            c = get_backfill_cursor(rid)

            if c is None:
                # Never backfilled — pick this one
                best_rider = conn
                cursor = None
                break

            if c.year <= EARLIEST_YEAR:
                continue  # Fully backfilled

            if cursor is None or c > cursor:
                cursor = c
                best_rider = conn

    if best_rider is None:
        msg = 'All riders fully backfilled'
        current_app.logger.info(msg)
        return {'status': msg}

    rider_id = best_rider['rider_id']
    rider_name = best_rider.get('rider_name', f'Rider {rider_id}')

    if cursor is None:
        # First backfill — start from the oldest activity we have, or now
        oldest = get_oldest_activity_date(rider_id)
        if oldest is None:
            # No activities at all — pull last year
            target_dt = datetime.utcnow() - timedelta(days=365)
            after_epoch = int(target_dt.timestamp())
            before_epoch = None
            cursor_after = date.today() - timedelta(days=365)
            current_app.logger.info(
                f'Backfill: {rider_name} has no activities, pulling last 365 days'
            )
        else:
            if isinstance(oldest, str):
                oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                oldest_dt = oldest.replace(tzinfo=None) if hasattr(oldest, 'tzinfo') and oldest.tzinfo else oldest
            before_epoch = int(oldest_dt.timestamp())
            target_dt = oldest_dt - timedelta(days=BACKFILL_DAYS)
            after_epoch = int(target_dt.timestamp())
            cursor_after = target_dt.date()
            current_app.logger.info(
                f'Backfill: {rider_name} first backfill, oldest activity is {oldest_dt.date()}, '
                f'fetching {BACKFILL_DAYS} days before that'
            )
    else:
        # Continue from where we left off
        before_epoch = int(datetime.combine(cursor, datetime.min.time()).timestamp())
        target = cursor - timedelta(days=BACKFILL_DAYS)
        after_epoch = int(datetime.combine(target, datetime.min.time()).timestamp())
        cursor_after = target
        current_app.logger.info(
            f'Backfill: {rider_name} cursor={cursor}, '
            f'fetching {BACKFILL_DAYS} days before that '
            f'(after={target}, before={cursor})'
        )

    try:
        counts = sync_rider_activities(
            rider_id=rider_id,
            after_epoch=after_epoch,
            before_epoch=before_epoch,
            calculate_eddington=True,
        )

        # Always advance the cursor, even if 0 activities found (gaps in history)
        update_backfill_cursor(rider_id, cursor_after)

        result = {
            'rider_id': rider_id,
            'name': rider_name,
            'cursor_moved_to': str(cursor_after),
            'days_back': BACKFILL_DAYS,
            'new': counts['new'],
            'updated': counts['updated'],
            'total_fetched': counts['total'],
        }
        current_app.logger.info(
            f'Backfill complete for {rider_name}: '
            f'{counts["new"]} new, {counts["updated"]} updated, '
            f'cursor now at {result["cursor_moved_to"]}'
        )
        return result
    except Exception as e:
        try:
            from models import get_db
            get_db().rollback()
        except Exception:
            pass
        current_app.logger.error(f'Backfill failed for {rider_name}: {e}')
        return {'rider_id': rider_id, 'name': rider_name, 'error': str(e)}


@cron_bp.route('/finalize-rides', methods=['POST'])
def finalize_rides():
    """Auto-finalize past rides: mark GOING riders as FINISHED.

    Called daily by GitHub Actions to ensure ride results are recorded.
    Admins can then fix DNF/DNS/OTL via the admin dashboard.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    from models import auto_finalize_past_rides

    try:
        results = auto_finalize_past_rides()
        total = sum(r['riders_finalized'] for r in results)
        current_app.logger.info(
            f'Finalized {total} riders across {len(results)} rides'
        )
        return jsonify({
            'finalized_rides': len(results),
            'total_riders': total,
            'details': results,
        }), 200
    except Exception as e:
        current_app.logger.error(f'Finalize rides failed: {e}')
        return jsonify({'error': str(e)}), 500


@cron_bp.route('/sync-rusa-results', methods=['POST'])
def sync_rusa_results():
    """Sync official finish times from RUSA for completed rides.

    Scrapes rusa.org for each rider with FINISHED rides missing a finish_time.
    Called daily by GitHub Actions, scheduled after finalize-rides.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    from models import sync_rusa_finish_times

    try:
        results = sync_rusa_finish_times()
        total_synced = sum(r['results_found'] for r in results)
        current_app.logger.info(
            f'RUSA sync: {total_synced} finish times updated '
            f'across {len(results)} riders'
        )
        return jsonify({
            'synced': total_synced,
            'riders_checked': len(results),
            'details': results,
        }), 200
    except Exception as e:
        current_app.logger.error(f'RUSA sync failed: {e}')
        return jsonify({'error': str(e)}), 500


@cron_bp.route('/backfill-wind', methods=['GET', 'POST'])
def backfill_wind():
    """Backfill historical wind data for past rides missing wind records.

    Processes rides from the current and previous season that have linked
    ride plans with RWGPS routes. Skips rides that already have wind data.
    Rate-limited to avoid overwhelming Open-Meteo API.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    import re
    from services.rwgps import fetch_route
    from services.weather import (
        get_stop_coordinates, get_historical_stop_wind, wind_cell_style,
    )
    from models import (
        get_db, get_ride_wind_data, save_ride_wind_data,
    )

    conn = get_db()
    cur = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)

    # Find past rides with plans but no wind data
    cur.execute("""
        SELECT r.id, r.name, r.date, r.distance_km,
               rp.id as plan_id, rp.slug as plan_slug,
               r.rwgps_url, r.rwgps_url_team
        FROM ride r
        JOIN ride_plan rp ON r.ride_plan_id = rp.id
        JOIN season s ON r.season_id = s.id
        WHERE (s.is_current = true
               OR s.name IN (SELECT name FROM season WHERE is_current = false ORDER BY name DESC LIMIT 1))
        AND r.date < CURRENT_DATE
        AND NOT EXISTS (
            SELECT 1 FROM ride_wind_data rwd WHERE rwd.ride_id = r.id
        )
        ORDER BY r.date DESC
        LIMIT 10
    """)
    rides = cur.fetchall()

    if not rides:
        return jsonify({'message': 'No rides need wind backfill', 'processed': 0}), 200

    results = []
    for ride in rides:
        ride_result = {'ride_id': ride['id'], 'name': ride['name'], 'date': str(ride['date'])}
        try:
            rwgps_url = ride['rwgps_url_team'] or ride['rwgps_url']
            match = re.search(r'/routes/(\d+)', rwgps_url) if rwgps_url else None
            if not match:
                ride_result['status'] = 'skip_no_route'
                results.append(ride_result)
                continue

            route_id = int(match.group(1))
            route_data = fetch_route(route_id)
            track_points = route_data.get('track_points', []) if route_data else []

            if not track_points:
                ride_result['status'] = 'skip_no_track'
                results.append(ride_result)
                continue

            # Get plan stops
            cur.execute("""
                SELECT stop_name, distance_miles, arrival_time_min
                FROM ride_plan_stop
                WHERE ride_plan_id = %s ORDER BY stop_order
            """, (ride['plan_id'],))
            plan_stops = [dict(row) for row in cur.fetchall()]

            if not plan_stops:
                ride_result['status'] = 'skip_no_stops'
                results.append(ride_result)
                continue

            ride_date = ride['date']
            if isinstance(ride_date, str):
                ride_date = date.fromisoformat(ride_date)

            wind_rows, data_source = get_historical_stop_wind(
                stops=plan_stops,
                track_points=track_points,
                ride_date=ride_date,
                ride_id=ride['id'],
            )

            if wind_rows:
                ride_result['status'] = 'ok'
                ride_result['stops'] = len(wind_rows)
                ride_result['source'] = data_source
            else:
                ride_result['status'] = 'skip_no_data'

        except Exception as e:
            current_app.logger.exception(
                "backfill-wind: failed for ride %s", ride['id']
            )
            ride_result['status'] = f'error: {str(e)[:100]}'

        results.append(ride_result)
        time.sleep(1)  # Rate limit

    ok_count = sum(1 for r in results if r.get('status') == 'ok')
    return jsonify({
        'processed': len(results),
        'success': ok_count,
        'results': results,
    }), 200


@cron_bp.route('/backfill-strava-streams', methods=['POST'])
def backfill_strava_streams():
    """Backfill Strava stream data for finished rides.

    Lightweight endpoint designed for Vercel's timeout limits.
    Run with ?phase= to control which step executes:

      ?phase=sync   — Sync Strava activities for ONE rider (use &rider_id=N).
                       Syncs 30 days per call. Run repeatedly until done=true.
      ?phase=match   — Auto-match unmatched finished rides to Strava activities.
                       No Strava API calls — pure DB lookups.
      ?phase=streams — Fetch and cache streams for matched rides (default).
                       Stops on rate limit. Use &limit=N (default 5, max 20).
      (no phase)     — Runs match + streams (the fast path).

    All phases are idempotent — safe to run repeatedly.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    from models import (
        _execute, create_strava_ride_match,
        RideStatus,
    )
    from services.strava_analysis import find_matching_activity, fetch_and_analyze

    phase = request.args.get('phase', '')
    results = {}

    # ── Phase: sync — sync one rider's Strava activities in 90-day chunks ─
    if phase == 'sync':
        from services.strava import sync_rider_activities
        from models import get_strava_connection

        rider_id = request.args.get('rider_id')
        if not rider_id:
            return jsonify({'error': 'rider_id required for phase=sync'}), 400

        rider_id = int(rider_id)

        # Find earliest finished ride for this rider
        earliest_row = _execute("""
            SELECT MIN(ri.date) as earliest
            FROM rider_ride rr
            JOIN ride ri ON ri.id = rr.ride_id
            WHERE rr.rider_id = %s AND rr.status = %s
        """, (rider_id, RideStatus.FINISHED.value)).fetchone()

        earliest = earliest_row['earliest'] if earliest_row else None
        if not earliest:
            return jsonify({'message': 'No finished rides for this rider', 'rider_id': rider_id}), 200

        if isinstance(earliest, str):
            earliest = date.fromisoformat(earliest)

        # Find how far back we've already synced (most recent activity after epoch)
        connection = get_strava_connection(rider_id)
        if not connection:
            return jsonify({'error': 'No Strava connection for this rider'}), 400

        # Check earliest synced activity to know where to resume
        earliest_synced = _execute("""
            SELECT MIN(start_date) as earliest_synced
            FROM strava_activity
            WHERE rider_id = %s
        """, (rider_id,)).fetchone()

        if earliest_synced and earliest_synced['earliest_synced']:
            sync_from = earliest_synced['earliest_synced']
            if isinstance(sync_from, str):
                sync_from = datetime.fromisoformat(sync_from).date()
            elif hasattr(sync_from, 'date'):
                sync_from = sync_from.date()
        else:
            sync_from = date.today()

        # If already synced past earliest ride, we're done
        if sync_from <= earliest:
            return jsonify({
                'phase': 'sync', 'rider_id': rider_id,
                'message': f'Already synced back to {sync_from}, earliest ride is {earliest}',
                'done': True,
            }), 200

        # Sync 30-day chunk ending at our earliest synced activity
        chunk_end = sync_from
        chunk_start = chunk_end - timedelta(days=30)
        after_epoch = int(datetime.combine(chunk_start, datetime.min.time()).timestamp())
        before_epoch = int(datetime.combine(chunk_end, datetime.min.time()).timestamp())

        try:
            sync_result = sync_rider_activities(
                rider_id, after_epoch=after_epoch, before_epoch=before_epoch,
                calculate_eddington=False,
            )
            done = chunk_start <= earliest
            return jsonify({
                'phase': 'sync',
                'rider_id': rider_id,
                'chunk': f'{chunk_start} to {chunk_end}',
                'target': str(earliest),
                'done': done,
                **sync_result,
            }), 200
        except Exception as e:
            return jsonify({'phase': 'sync', 'rider_id': rider_id, 'error': str(e)[:200]}), 500

    # ── Phase: match — auto-match unmatched finished rides ──────────────
    if phase in ('match', ''):
        unmatched_rides = _execute("""
            SELECT rr.rider_id, rr.ride_id,
                   ri.date, ri.distance_km, ri.name as ride_name,
                   r.first_name
            FROM rider_ride rr
            JOIN ride ri ON ri.id = rr.ride_id
            JOIN rider r ON r.id = rr.rider_id
            JOIN strava_connection sc ON sc.rider_id = rr.rider_id
            LEFT JOIN strava_ride_match srm ON srm.rider_id = rr.rider_id AND srm.ride_id = rr.ride_id
            WHERE rr.status = %s
              AND srm.id IS NULL
            ORDER BY ri.date DESC
        """, (RideStatus.FINISHED.value,)).fetchall()

        matching_results = []
        for row in unmatched_rides:
            ride_date = row['date']
            if isinstance(ride_date, str):
                ride_date = date.fromisoformat(ride_date)
            try:
                match = find_matching_activity(
                    rider_id=row['rider_id'],
                    ride_date=ride_date,
                    ride_distance_km=row['distance_km'],
                    ride_name=row['ride_name'] or '',
                )
                if match:
                    create_strava_ride_match(row['rider_id'], row['ride_id'], match['strava_activity_id'])
                    matching_results.append({
                        'rider': row['first_name'], 'ride': row['ride_name'],
                        'date': str(row['date']), 'status': 'matched',
                    })
                else:
                    matching_results.append({
                        'rider': row['first_name'], 'ride': row['ride_name'],
                        'date': str(row['date']), 'status': 'no_match',
                    })
            except Exception as e:
                current_app.logger.warning(f'Match failed: rider={row["rider_id"]} ride={row["ride_id"]}: {e}')

        results['matching'] = matching_results

        # If only match phase requested, return now
        if phase == 'match':
            matched = sum(1 for r in matching_results if r.get('status') == 'matched')
            return jsonify({
                'phase': 'match',
                'matched': matched,
                'unmatched': len(matching_results) - matched,
                'details': matching_results,
            }), 200

    # ── Phase: streams — fetch and cache missing stream data ────────────
    fetch_limit = min(int(request.args.get('limit', 5)), 20)

    missing_streams = _execute("""
        SELECT srm.id AS match_id, srm.rider_id, srm.strava_activity_id,
               ri.name as ride_name, ri.date, r.first_name
        FROM strava_ride_match srm
        JOIN rider_ride rr ON rr.rider_id = srm.rider_id AND rr.ride_id = srm.ride_id
        JOIN ride ri ON ri.id = srm.ride_id
        JOIN rider r ON r.id = srm.rider_id
        LEFT JOIN strava_ride_analysis sra ON sra.match_id = srm.id
        WHERE rr.status = %s
          AND (sra.id IS NULL OR (sra.activity_streams IS NULL AND sra.strava_api_error IS NULL))
        ORDER BY ri.date DESC
        LIMIT %s
    """, (RideStatus.FINISHED.value, fetch_limit)).fetchall()

    stream_results = []
    rate_limited = False
    for row in missing_streams:
        if rate_limited:
            break
        try:
            result = fetch_and_analyze(
                rider_id=row['rider_id'],
                match_id=row['match_id'],
                strava_activity_id=row['strava_activity_id'],
            )
            error = result.get('error', '')
            if error and 'rate limit' in error.lower():
                rate_limited = True
                stream_results.append({
                    'rider': row['first_name'], 'ride': row['ride_name'],
                    'date': str(row['date']), 'status': 'rate_limited',
                })
            elif error:
                stream_results.append({
                    'rider': row['first_name'], 'ride': row['ride_name'],
                    'date': str(row['date']), 'status': 'error', 'error': error[:100],
                })
            else:
                stream_results.append({
                    'rider': row['first_name'], 'ride': row['ride_name'],
                    'date': str(row['date']), 'status': 'cached',
                })
        except Exception as e:
            current_app.logger.warning(f'Stream fetch failed: match={row["match_id"]}: {e}')
            stream_results.append({
                'rider': row['first_name'], 'ride': row['ride_name'],
                'status': 'error', 'error': str(e)[:100],
            })

    results['streams'] = stream_results

    matching_results = results.get('matching', [])
    return jsonify({
        'summary': {
            'rides_matched': sum(1 for r in matching_results if r.get('status') == 'matched'),
            'rides_unmatched': sum(1 for r in matching_results if r.get('status') == 'no_match'),
            'streams_cached': sum(1 for r in stream_results if r.get('status') == 'cached'),
            'streams_errors': sum(1 for r in stream_results if r.get('status') == 'error'),
            'rate_limited': rate_limited,
        },
        'details': results,
    }), 200


@cron_bp.route('/fetch-route-weather', methods=['GET', 'POST'])
def fetch_route_weather_cron():
    """Pre-fetch Open-Meteo weather for upcoming + live rides and store it (TA-237).

    Runs hourly (GitHub Actions). Selects rides within 28 days (+ live/active rides) that
    resolve to an RWGPS route, then fetches Open-Meteo ONLY for those within the 16-day
    forecast horizon, samples a dense (15 km) route forecast, and upserts it into
    route_weather_cache. Rides 17-28 days out are reported skipped_beyond_horizon and
    picked up automatically once they cross the horizon (hourly cadence -> self-healing).

    Per-route fail-soft: a fetch error keeps the last-good stored row (the upsert is simply
    skipped), logs a warning, and never raises — so no user request ever fetches weather
    live (removing the TLS-handshake hangs that hit the brevet calendar). Optional
    ?limit=N caps how many routes are fetched per run and REPORTS truncation.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    import re
    from services.rwgps import fetch_route
    from services.weather import sample_track_points, fetch_route_weather
    from models import get_upcoming_weather_targets, save_route_weather_cache
    from shared.live_radial import track_from_route

    FORECAST_HORIZON_DAYS = 16   # Open-Meteo forecasts at most 16 days ahead
    SELECT_WINDOW_DAYS = 28      # rides we consider (mission wording / calendar cutoff)
    SAMPLE_INTERVAL_M = 15000    # dense 15 km sampling (matches weather page / live charts)
    ELEVATION_TRACK_POINTS = 800  # downsample cap for the cached rpv2 elevation track

    try:
        targets = get_upcoming_weather_targets(within_days=SELECT_WINDOW_DAYS)
    except Exception as e:
        current_app.logger.error('fetch-route-weather: failed to load targets: %s', e)
        return jsonify({'error': 'Database error'}), 500

    try:
        limit = int(request.args.get('limit', 0))
    except (TypeError, ValueError):
        limit = 0

    today = date.today()
    horizon_cutoff = today + timedelta(days=FORECAST_HORIZON_DAYS)

    # Dedup on (route_id, forecast_date) — several riders/events can share a route+date —
    # and gate on the forecast horizon before any fetch.
    seen = set()
    plan_targets = []
    skipped_beyond_horizon = 0
    skipped_no_route = 0
    for t in targets:
        forecast_date = t.get('forecast_date')
        if isinstance(forecast_date, str):
            forecast_date = date.fromisoformat(forecast_date)
        if forecast_date is None:
            continue
        match = re.search(r'/routes/(\d+)', t.get('rwgps_url') or '')
        if not match:
            skipped_no_route += 1
            continue
        route_id = int(match.group(1))
        key = (route_id, forecast_date)
        if key in seen:
            continue
        seen.add(key)
        if forecast_date < today:
            continue  # a live multi-day ride that started earlier — nothing to forecast
        if forecast_date > horizon_cutoff:
            skipped_beyond_horizon += 1
            continue
        plan_targets.append((route_id, forecast_date, t.get('name')))

    truncated = False
    if limit and len(plan_targets) > limit:
        truncated = True
        current_app.logger.warning(
            'fetch-route-weather: %d routes in horizon, truncating to limit=%d',
            len(plan_targets), limit)
        plan_targets = plan_targets[:limit]

    succeeded = 0
    failed = 0
    details = []
    for route_id, forecast_date, name in plan_targets:
        entry = {'route_id': route_id, 'date': str(forecast_date), 'name': name}
        try:
            route_data = fetch_route(route_id)
            track_points = (route_data or {}).get('track_points') or []
            if not track_points:
                entry['status'] = 'skip_no_track'
                details.append(entry)
                continue
            samples = sample_track_points(track_points, interval_m=SAMPLE_INTERVAL_M)
            if not samples:
                entry['status'] = 'skip_no_samples'
                details.append(entry)
                continue
            # Request enough days to REACH the ride date plus a buffer for multi-day
            # brevets (a 600k spans ~2 days), and never fewer than Open-Meteo's old 7-day
            # default so a same-day multi-day ride doesn't lose its day-2 hours. Capped at
            # the 16-day forecast horizon.
            forecast_days = min(FORECAST_HORIZON_DAYS,
                                max(7, (forecast_date - today).days + 3))
            weather_data = fetch_route_weather(samples, forecast_days=forecast_days)
            if not weather_data:
                entry['status'] = 'skip_no_data'
                details.append(entry)
                continue
            # Downsampled elevation track for the rpv2 gradient elevation profile, built
            # from the SAME route_data already fetched here — so the plan render reads it
            # from cache instead of fetching RWGPS live (TA-237). None → empty profile.
            elevation_track = track_from_route(
                route_data, max_points=ELEVATION_TRACK_POINTS) or None
            save_route_weather_cache(route_id, forecast_date, weather_data, samples,
                                     elevation_track)
            entry['status'] = 'ok'
            entry['samples'] = len(samples)
            succeeded += 1
        except Exception as e:
            # Fail-soft: keep the last-good stored row (skip the upsert), log, never raise.
            failed += 1
            entry['status'] = f'error: {str(e)[:120]}'
            current_app.logger.warning(
                'fetch-route-weather: route %s date %s failed (last-good kept): %s',
                route_id, forecast_date, e)
        details.append(entry)
        time.sleep(0.5)  # gentle rate limit on Open-Meteo

    current_app.logger.info(
        'fetch-route-weather: %d ok, %d failed, %d beyond-horizon, %d no-route',
        succeeded, failed, skipped_beyond_horizon, skipped_no_route)
    return jsonify({
        'succeeded': succeeded,
        'failed': failed,
        'skipped_beyond_horizon': skipped_beyond_horizon,
        'skipped_no_route': skipped_no_route,
        'truncated': truncated,
        'processed': len(details),
        'details': details,
    }), 200


# Downsample cap + re-warm window for the route-geometry (elevation) cache. Route
# geometry is near-static, so a generous freshness window keeps the cron cheap; ?force=1
# bypasses it for an on-demand re-warm.
ELEVATION_CACHE_POINTS = 800
ELEVATION_CACHE_FRESH_DAYS = 30


@cron_bp.route('/warm-plan-elevation', methods=['GET', 'POST'])
def warm_plan_elevation_cron():
    """Cache the RWGPS elevation track for EVERY route referenced by a ride_plan, so the
    rpv2 plan-page gradient profile renders for ANY plan (past or upcoming) without a live
    RWGPS fetch on the request path (the TA-237 read-from-cache invariant). Unlike
    fetch-route-weather (upcoming events only), this warms all base-plan routes — custom
    plans reuse their base route, so enumerating ride_plan covers them.

    Auth-gated (Bearer CRON_SECRET). Idempotent: a route warmed within
    ELEVATION_CACHE_FRESH_DAYS is skipped unless ?force=1. Fail-soft per route: an RWGPS
    error keeps the last-good row and is counted, never 500s the cron. Returns
    {ok, warmed, skipped, failed, considered}.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    from datetime import datetime, timezone, timedelta
    from services.rwgps import fetch_route
    from shared.rwgps import extract_rwgps_route_id
    from shared.live_radial import track_from_route
    from models import (get_all_ride_plans, upsert_route_geometry,
                        get_route_geometry_freshness)

    force = request.args.get('force') in ('1', 'true', 'yes')

    # Distinct RWGPS route ids across all base plans — from the stored numeric id and
    # both url columns (the render may look up rwgps_url or rwgps_url_team).
    try:
        plans = get_all_ride_plans()
    except Exception as e:
        current_app.logger.warning('warm-plan-elevation: plan load failed: %s', e)
        return jsonify({'ok': False, 'error': 'plan load failed',
                        'warmed': 0, 'skipped': 0, 'failed': 0}), 200

    route_ids = set()
    for p in plans:
        rid = p.get('rwgps_route_id')
        if rid:
            route_ids.add(str(rid))
        for col in ('rwgps_url', 'rwgps_url_team'):
            rid = extract_rwgps_route_id(p.get(col))
            if rid:
                route_ids.add(str(rid))

    warmed = skipped = failed = 0
    for route_id in sorted(route_ids):
        try:
            if not force:
                fetched_at = get_route_geometry_freshness(route_id)
                if fetched_at and (datetime.now(timezone.utc) - fetched_at
                                   < timedelta(days=ELEVATION_CACHE_FRESH_DAYS)):
                    skipped += 1
                    continue
            route_data = fetch_route(route_id)
            elevation_track = track_from_route(
                route_data, max_points=ELEVATION_CACHE_POINTS) or None
            upsert_route_geometry(route_id, elevation_track)
            warmed += 1
            time.sleep(0.3)  # gentle rate limit on RWGPS
        except Exception as e:
            failed += 1
            current_app.logger.warning(
                'warm-plan-elevation: route %s failed (last-good kept): %s', route_id, e)

    current_app.logger.info(
        'warm-plan-elevation: %d warmed, %d skipped, %d failed (of %d routes)',
        warmed, skipped, failed, len(route_ids))
    return jsonify({'ok': True, 'warmed': warmed, 'skipped': skipped,
                    'failed': failed, 'considered': len(route_ids)}), 200


@cron_bp.route('/poll-garmin-livetrack', methods=['POST'])
def poll_garmin_livetrack():
    """Poll Garmin LiveTrack for opted-in riders, store positions, purge old data.

    Called ~every 3 min by GitHub Actions (Vercel crons have a 24h minimum).
    Fail-soft per rider: one bad/expired session never breaks the batch.
    """
    auth_error = _verify_cron_auth()
    if auth_error:
        return auth_error

    from models import (get_enabled_live_tracking, insert_live_position,
                        purge_old_positions, get_last_position_recorded_at)
    from services.garmin_livetrack import parse_session, fetch_positions

    RETENTION_DAYS = 7
    MIN_GAP_SECONDS = 30   # downsample: keep at most one stored point per 30s

    try:
        tracked = get_enabled_live_tracking()
    except Exception as e:
        current_app.logger.error('poll-garmin-livetrack: failed to load riders: %s', e)
        return jsonify({'error': 'Database error'}), 500

    polled = 0
    inserted = 0
    errors = []
    for row in tracked:
        rider_id = row['rider_id']
        ride_id = row.get('active_ride_id')
        token = row.get('garmin_session_token')
        session_url = row.get('garmin_session_url')
        # Prefer the stored token; re-derive session_id from the saved URL.
        parsed = parse_session(session_url) if session_url else None
        session_id = parsed['session_id'] if parsed else None
        if not token or not session_id:
            errors.append({'rider_id': rider_id, 'error': 'missing token/session_id'})
            continue
        if not ride_id:
            # No ride to attribute points to — nothing would show on a map.
            errors.append({'rider_id': rider_id, 'error': 'no active ride'})
            continue

        polled += 1
        try:
            points = fetch_positions(token, session_id)
        except Exception as e:
            current_app.logger.warning('poll-garmin-livetrack: rider %s fetch failed: %s', rider_id, e)
            errors.append({'rider_id': rider_id, 'error': str(e)[:200]})
            continue

        # Append NEW trackpoints (since the last stored one FOR THIS RIDE),
        # downsampled to at most one per MIN_GAP_SECONDS, so we accumulate a real
        # position history for elapsed/moving/stopped — not just the latest point.
        last_at = get_last_position_recorded_at(rider_id, ride_id)
        fresh = sorted(
            (p for p in points if p.get('recorded_at') is not None
             and (last_at is None or p['recorded_at'] > last_at)),
            key=lambda p: p['recorded_at'],
        )
        kept_at = None
        rider_inserted = 0
        for p in fresh:
            if kept_at is not None and (p['recorded_at'] - kept_at).total_seconds() < MIN_GAP_SECONDS:
                continue
            if insert_live_position(
                rider_id=rider_id,
                lat=p['lat'], lng=p['lng'],
                recorded_at=p['recorded_at'], source='garmin',
                speed=p.get('speed'), heart_rate=p.get('heart_rate'),
                power=p.get('power'), cadence=p.get('cadence'),
                ride_id=ride_id,
            ):
                kept_at = p['recorded_at']
                rider_inserted += 1
        inserted += rider_inserted

    try:
        purged = purge_old_positions(RETENTION_DAYS)
    except Exception as e:
        current_app.logger.error('poll-garmin-livetrack: purge failed: %s', e)
        purged = None

    current_app.logger.info(
        'poll-garmin-livetrack: polled=%d inserted=%d errors=%d purged=%s',
        polled, inserted, len(errors), purged,
    )
    return jsonify({
        'polled': polled,
        'inserted': inserted,
        'errors': errors,
        'purged': purged,
    }), 200
