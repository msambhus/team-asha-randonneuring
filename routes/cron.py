"""Cron endpoints for scheduled background tasks."""
import os
import time
from flask import Blueprint, request, jsonify, current_app

cron_bp = Blueprint('cron', __name__)


@cron_bp.route('/sync-strava', methods=['POST'])
def sync_strava():
    """Periodic Strava sync endpoint (called by GitHub Actions).

    Syncs Strava activities for all riders with active connections.
    Protected by CRON_SECRET environment variable.

    Returns:
        JSON with sync results: {synced: int, failed: int, skipped: int, errors: list}
    """
    # 1. Verify authentication
    auth_header = request.headers.get('Authorization', '')
    expected_secret = current_app.config.get('CRON_SECRET')

    if not expected_secret:
        return jsonify({'error': 'CRON_SECRET not configured'}), 500

    expected_token = f'Bearer {expected_secret}'

    if not auth_header or auth_header != expected_token:
        current_app.logger.warning(f'Unauthorized cron request from {request.remote_addr}')
        return jsonify({'error': 'Unauthorized'}), 401

    # 2. Get all riders with active Strava connection
    from models import get_all_active_strava_connections

    try:
        connections = get_all_active_strava_connections()
        current_app.logger.info(f'Found {len(connections)} active Strava connections to sync')
    except Exception as e:
        current_app.logger.error(f'Failed to fetch active connections: {e}')
        return jsonify({'error': 'Database error', 'detail': str(e)}), 500

    # 3. Sync with rate limiting
    from services.strava import sync_rider_activities

    results = {
        'synced': 0,
        'failed': 0,
        'skipped': 0,
        'errors': [],
    }

    # Limit per run to avoid rate limits (50 riders × ~2 requests = ~100 requests)
    # Well within Strava's 200 req/15min limit
    MAX_RIDERS_PER_RUN = 50

    connections_to_sync = connections[:MAX_RIDERS_PER_RUN]
    if len(connections) > MAX_RIDERS_PER_RUN:
        results['skipped'] = len(connections) - MAX_RIDERS_PER_RUN

    current_app.logger.info(
        f'Syncing {len(connections_to_sync)} riders '
        f'(skipping {results["skipped"]} due to batch limit)'
    )

    for i, conn in enumerate(connections_to_sync):
        rider_id = conn['rider_id']

        try:
            # Sync last 7 days only (not full year) to reduce API calls
            count = sync_rider_activities(
                rider_id=rider_id,
                days=7,
                calculate_eddington=True,
            )

            results['synced'] += 1
            current_app.logger.info(
                f'Successfully synced rider {rider_id}: {count} activities'
            )

            # Add small delay between riders to avoid burst requests
            # This spreads 50 riders over ~50 seconds
            if i < len(connections_to_sync) - 1:
                time.sleep(1)

        except Exception as e:
            results['failed'] += 1
            error_msg = f'Rider {rider_id}: {str(e)}'
            results['errors'].append(error_msg)
            current_app.logger.error(f'Failed to sync rider {rider_id}: {e}')

            # If we hit rate limit, stop immediately
            if '429' in str(e) or 'rate limit' in str(e).lower():
                current_app.logger.error('Hit Strava rate limit - stopping batch')
                results['skipped'] += len(connections_to_sync) - i - 1
                break

    current_app.logger.info(
        f'Sync complete: {results["synced"]} synced, '
        f'{results["failed"]} failed, {results["skipped"]} skipped'
    )

    # Return 200 even if some failed (partial success)
    return jsonify(results), 200
