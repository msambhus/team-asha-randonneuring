"""Strava API service — OAuth token exchange, refresh, and activity fetching.

The club-agnostic, framework-free HTTP layer now lives in ``shared/strava.py`` so
both Team Asha and BrevetHub share a single implementation. This module keeps the
Flask/DB-coupled wrappers — reading Strava config from ``current_app``, persisting
refreshed tokens, and syncing activities into the DB — and delegates the pure
protocol work to ``shared.strava``. Every symbol previously importable from
``services.strava`` is preserved with identical behavior, and Team Asha's
``strava_connection.expires_at`` stays a Unix-epoch INTEGER end to end.
"""
import time

from flask import current_app

from shared import strava as _shared
# Pure, framework-free helpers re-exported unchanged so existing importers of
# `services.strava` keep resolving these names.
from shared.strava import transform_activity, deauthorize_strava  # noqa: F401


def exchange_code_for_token(code):
    """Exchange authorization code for access/refresh tokens.

    Returns:
        dict with athlete, access_token, refresh_token, expires_at
    """
    return _shared.exchange_code_for_token(
        code,
        client_id=current_app.config['STRAVA_CLIENT_ID'],
        client_secret=current_app.config['STRAVA_CLIENT_SECRET'],
        token_url=current_app.config['STRAVA_TOKEN_URL'],
    )


def _get_valid_token(connection):
    """Return a valid access token, refreshing if expired.

    Args:
        connection: dict with access_token, refresh_token, expires_at, rider_id

    Returns:
        str: valid access_token

    Side effect:
        Updates strava_connection row if token was refreshed.
    """
    if connection['expires_at'] > time.time() + 60:  # 60s buffer
        return connection['access_token']

    # Token expired or about to expire — refresh via the shared protocol layer.
    token_data = _shared.refresh_access_token(
        connection['refresh_token'],
        client_id=current_app.config['STRAVA_CLIENT_ID'],
        client_secret=current_app.config['STRAVA_CLIENT_SECRET'],
        token_url=current_app.config['STRAVA_TOKEN_URL'],
    )

    # Persist new tokens
    from models import update_strava_tokens
    update_strava_tokens(
        rider_id=connection['rider_id'],
        access_token=token_data['access_token'],
        refresh_token=token_data['refresh_token'],
        expires_at=token_data['expires_at'],
    )

    return token_data['access_token']


def fetch_activities(connection, after_epoch=None, before_epoch=None, per_page=100):
    """Fetch activities from Strava API.

    Args:
        connection: strava_connection row dict
        after_epoch: Unix timestamp to fetch activities after (default: 1 year ago)
        before_epoch: Unix timestamp to fetch activities before (default: None = now)
        per_page: Activities per API page (max 200)

    Returns:
        list of activity dicts from Strava API
    """
    token = _get_valid_token(connection)
    return _shared.fetch_activities(
        token,
        api_base=current_app.config['STRAVA_API_BASE'],
        after_epoch=after_epoch,
        before_epoch=before_epoch,
        per_page=per_page,
    )


def sync_athlete_profile(connection):
    """Refresh stable athlete-level metrics that Strava actually exposes."""
    token = _get_valid_token(connection)
    athlete = _shared.fetch_athlete(
        token, api_base=current_app.config['STRAVA_API_BASE'])
    from models import update_strava_athlete_metrics
    update_strava_athlete_metrics(
        connection['rider_id'],
        ftp=athlete.get('ftp'),
    )
    return athlete


def sync_rider_activities(rider_id, days=365, before_epoch=None, after_epoch=None, calculate_eddington=True):
    """Pull activities for a rider and upsert into DB.

    Args:
        rider_id: rider ID
        days: how many days back to fetch (default: 365 = 1 year). Ignored if after_epoch is set.
        before_epoch: Unix timestamp — only fetch activities before this time (for backfill)
        after_epoch: Unix timestamp — only fetch activities after this time. Overrides days.
        calculate_eddington: whether to recalculate Eddington number after sync

    Returns:
        dict: {'new': int, 'updated': int, 'failed': int, 'total': int}
    """
    from models import (get_strava_connection, upsert_strava_activity,
                        update_strava_last_sync, get_all_strava_activities_for_eddington,
                        update_eddington_number)

    connection = get_strava_connection(rider_id)
    if not connection:
        return {'new': 0, 'updated': 0, 'failed': 0, 'total': 0}

    try:
        sync_athlete_profile(connection)
    except Exception as e:
        # Athlete metrics are additive; activity sync must still proceed.
        current_app.logger.warning(
            "Strava athlete profile sync failed for rider %s: %s",
            rider_id, e)

    if after_epoch is None:
        after_epoch = int(time.time()) - (days * 24 * 3600)
    activities = fetch_activities(connection, after_epoch=after_epoch, before_epoch=before_epoch)
    new_count = 0
    updated_count = 0
    failed_count = 0
    for activity in activities:
        row = transform_activity(activity, rider_id)
        try:
            is_new = upsert_strava_activity(row)
            if is_new:
                new_count += 1
            else:
                updated_count += 1
        except Exception as e:
            failed_count += 1
            try:
                from models import get_db
                get_db().rollback()
            except Exception:
                pass
            current_app.logger.warning(
                "Failed to upsert activity for rider %s: %s",
                rider_id, e
            )

    update_strava_last_sync(rider_id)

    total = new_count + updated_count
    # Calculate Eddington number after sync
    if calculate_eddington:
        try:
            from services.eddington import calculate_eddington_number
            # The activity query is memoized. Invalidate its rider-specific
            # entry after upserts so a manual sync calculates from fresh rows.
            from cache import cache
            cache.delete_memoized(get_all_strava_activities_for_eddington, rider_id)
            all_activities = get_all_strava_activities_for_eddington(rider_id)

            eddington_miles = calculate_eddington_number(all_activities, unit='miles')
            eddington_km = calculate_eddington_number(all_activities, unit='km')

            update_eddington_number(rider_id, eddington_miles, eddington_km)
        except Exception as e:
            current_app.logger.warning("Eddington calculation failed for rider %s: %s", rider_id, e)

    return {'new': new_count, 'updated': updated_count, 'failed': failed_count, 'total': len(activities)}
