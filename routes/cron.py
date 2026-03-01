"""Cron endpoints for scheduled background tasks."""
import os
import time
from datetime import datetime, timedelta, timezone
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
            count = sync_rider_activities(
                rider_id=rider_id,
                days=7,
                calculate_eddington=True,
            )

            results['synced'] += 1
            results['details'].append({
                'rider_id': rider_id,
                'name': rider_name,
                'activities': count,
            })
            current_app.logger.info(
                f'Synced {rider_name} (id={rider_id}): {count} activities'
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

    # --- Phase 2: Gradual backfill (one rider per run) ---
    # Pick the rider whose oldest activity is most recent (least history fetched)
    # and fetch 90 more days further back
    try:
        backfill_result = _do_gradual_backfill(connections_to_sync)
        results['backfill'] = backfill_result
    except Exception as e:
        current_app.logger.error(f'Backfill failed: {e}')
        results['backfill'] = {'error': str(e)}

    current_app.logger.info(
        f'Sync complete: {results["synced"]} synced, '
        f'{results["failed"]} failed, {results["skipped"]} skipped'
    )

    return jsonify(results), 200


def _do_gradual_backfill(connections):
    """Backfill one rider per run, going 90 days further back each time.

    Strategy: Find the rider with the least historical data (newest oldest-activity)
    and fetch 90 days before their oldest activity. Over many runs, all riders
    will gradually accumulate full history.

    Returns:
        dict with backfill details
    """
    from models import get_oldest_activity_date
    from services.strava import sync_rider_activities

    # Strava was founded in 2009; don't go back further than 2008
    EARLIEST_YEAR = 2008
    BACKFILL_DAYS = 90

    # Find rider with least history (most recent oldest activity)
    best_rider = None
    best_oldest = None

    for conn in connections:
        rider_id = conn['rider_id']
        rider_name = conn.get('rider_name', f'Rider {rider_id}')
        oldest = get_oldest_activity_date(rider_id)

        if oldest is None:
            # No activities at all — this rider needs a full initial pull
            best_rider = conn
            best_oldest = None
            break

        # Parse the date
        if isinstance(oldest, str):
            oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
        else:
            oldest_dt = oldest

        # Skip if we've already gone back to 2008
        if oldest_dt.year <= EARLIEST_YEAR:
            continue

        if best_oldest is None or oldest_dt > best_oldest:
            best_oldest = oldest_dt
            best_rider = conn

    if best_rider is None:
        msg = 'All riders fully backfilled'
        current_app.logger.info(msg)
        return {'status': msg}

    rider_id = best_rider['rider_id']
    rider_name = best_rider.get('rider_name', f'Rider {rider_id}')

    if best_oldest is None:
        # No activities — do a big initial pull (1 year)
        days_back = 365
        current_app.logger.info(
            f'Backfill: {rider_name} has no activities, pulling last {days_back} days'
        )
    else:
        # Calculate how many days back from today to go 90 days before oldest activity
        now = datetime.now(timezone.utc)
        target = best_oldest - timedelta(days=BACKFILL_DAYS)
        days_back = (now - target).days
        current_app.logger.info(
            f'Backfill: {rider_name} oldest activity is {best_oldest.date()}, '
            f'fetching {BACKFILL_DAYS} days further back (days_back={days_back})'
        )

    try:
        count = sync_rider_activities(
            rider_id=rider_id,
            days=days_back,
            calculate_eddington=True,
        )
        result = {
            'rider_id': rider_id,
            'name': rider_name,
            'oldest_before': str(best_oldest.date()) if best_oldest else None,
            'days_back': days_back,
            'activities': count,
        }
        current_app.logger.info(f'Backfill complete for {rider_name}: {count} activities')
        return result
    except Exception as e:
        try:
            from models import get_db
            get_db().rollback()
        except Exception:
            pass
        current_app.logger.error(f'Backfill failed for {rider_name}: {e}')
        return {'rider_id': rider_id, 'name': rider_name, 'error': str(e)}
