"""Cron endpoints for scheduled background tasks."""
import os
import time
from datetime import date, datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, current_app

cron_bp = Blueprint('cron', __name__)


def _verify_cron_auth():
    """Verify CRON_SECRET authentication. Returns error response or None."""
    auth_header = request.headers.get('Authorization', '')
    expected_secret = current_app.config.get('CRON_SECRET')

    if not expected_secret:
        return jsonify({'error': 'CRON_SECRET not configured'}), 500

    if not auth_header or auth_header != f'Bearer {expected_secret}':
        current_app.logger.warning(f'Unauthorized cron request from {request.remote_addr}')
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
               rp.rwgps_url, rp.rwgps_url_team
        FROM ride r
        JOIN ride_plan rp ON r.ride_plan_id = rp.id
        JOIN season s ON r.season_id = s.id
        WHERE s.name IN (
            SELECT name FROM season ORDER BY id DESC LIMIT 2
        )
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
