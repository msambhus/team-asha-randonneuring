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

    # --- Phase 2: Gradual backfill (one rider per run) ---
    # Optional: pass {"rider_id": 6} in request body to force backfill a specific rider
    force_rider_id = (request.get_json(silent=True) or {}).get('rider_id')
    try:
        backfill_result = _do_gradual_backfill(connections_to_sync, force_rider_id=force_rider_id)
        results['backfill'] = backfill_result
    except Exception as e:
        current_app.logger.error(f'Backfill failed: {e}')
        results['backfill'] = {'error': str(e)}

    current_app.logger.info(
        f'Sync complete: {results["synced"]} synced, '
        f'{results["failed"]} failed, {results["skipped"]} skipped'
    )

    return jsonify(results), 200


def _do_gradual_backfill(connections, force_rider_id=None):
    """Backfill one rider per run, going 90 days further back each time.

    Args:
        connections: list of active strava connections
        force_rider_id: optional rider_id to backfill instead of auto-picking

    Returns:
        dict with backfill details
    """
    from models import get_oldest_activity_date
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
        oldest = get_oldest_activity_date(force_rider_id)
        if oldest is None:
            best_oldest = None
        else:
            if isinstance(oldest, str):
                best_oldest = datetime.fromisoformat(oldest.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                best_oldest = oldest.replace(tzinfo=None) if hasattr(oldest, 'tzinfo') and oldest.tzinfo else oldest
        rider_name = best_rider.get('rider_name', f'Rider {force_rider_id}')
        current_app.logger.info(f'Backfill: forced for {rider_name} (id={force_rider_id})')
    else:
        # Find rider with least history (most recent oldest activity)
        best_rider = None
        best_oldest = None

    if not force_rider_id:
        for conn in connections:
            rider_id = conn['rider_id']
            rider_name = conn.get('rider_name', f'Rider {rider_id}')
            oldest = get_oldest_activity_date(rider_id)

            if oldest is None:
                best_rider = conn
                best_oldest = None
                break

            if isinstance(oldest, str):
                oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
                if oldest_dt.tzinfo is not None:
                    oldest_dt = oldest_dt.replace(tzinfo=None)
            else:
                oldest_dt = oldest
                if hasattr(oldest_dt, 'tzinfo') and oldest_dt.tzinfo is not None:
                    oldest_dt = oldest_dt.replace(tzinfo=None)

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

    before_epoch = None
    if best_oldest is None:
        # No activities — do a big initial pull (1 year)
        days_back = 365
        current_app.logger.info(
            f'Backfill: {rider_name} has no activities, pulling last {days_back} days'
        )
    else:
        # Fetch ONLY the window: (oldest - 90 days) to oldest
        # before_epoch = oldest activity date (don't re-fetch what we have)
        # after_epoch  = oldest - 90 days (go further back)
        before_epoch = int(best_oldest.timestamp())
        now = datetime.utcnow()
        target = best_oldest - timedelta(days=BACKFILL_DAYS)
        days_back = (now - target).days
        current_app.logger.info(
            f'Backfill: {rider_name} oldest activity is {best_oldest.date()}, '
            f'fetching {BACKFILL_DAYS} days before that '
            f'(after={target.date()}, before={best_oldest.date()})'
        )

    try:
        counts = sync_rider_activities(
            rider_id=rider_id,
            days=days_back,
            before_epoch=before_epoch,
            calculate_eddington=True,
        )
        result = {
            'rider_id': rider_id,
            'name': rider_name,
            'oldest_before': str(best_oldest.date()) if best_oldest else None,
            'days_back': days_back,
            'new': counts['new'],
            'updated': counts['updated'],
            'total_fetched': counts['total'],
        }
        current_app.logger.info(
            f'Backfill complete for {rider_name}: '
            f'{counts["new"]} new, {counts["updated"]} updated'
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
