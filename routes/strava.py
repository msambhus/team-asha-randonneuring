"""Strava integration routes: OAuth connect, callback, sync, disconnect.

Connect has two modes. By default (``STRAVA_BROKER_ENABLED`` off) it redirects
straight to Strava and the OAuth code comes back to ``/strava/callback`` — the
original, still-supported rollback path. With the broker on, connect signs a
short-lived HMAC state and hands off to BrevetHub, which hosts the single Strava
callback for both apps; the tokens return via a one-time server-side handoff row
consumed at ``/strava/broker-return`` (never via a URL query param).
"""
from flask import (Blueprint, redirect, url_for, session, flash,
                   request, current_app)
from auth import profile_required
from urllib.parse import urlencode
import models
from services.strava import exchange_code_for_token, sync_rider_activities, deauthorize_strava
from shared.broker_state import sign_state
from cache import cache

strava_bp = Blueprint('strava', __name__)


def _run_post_connect_sync(rider_id):
    """Initial history sync + cache clear after a connection is written.

    Shared by the direct callback and the broker-return path so both behave
    identically. A sync failure is non-fatal — the connection is already stored
    and a later sync retries.
    """
    try:
        counts = sync_rider_activities(rider_id, days=365)
        cache.clear()  # Clear cache after Strava sync
        flash(f'Strava connected! Synced {counts["new"]} new activities.', 'success')
    except Exception as e:
        flash('Strava connected, but activity sync failed. We will retry later.', 'warning')
        current_app.logger.warning("Strava initial sync error for rider %s: %s", rider_id, e)


@strava_bp.route('/connect')
@profile_required
def connect():
    """Begin the Strava OAuth flow.

    Broker on: sign a state and 302 to BrevetHub's broker. Broker off (default):
    302 straight to Strava's authorization page (the rollback path).
    """
    rider_id = session.get('rider_id')

    # Check if already connected
    existing = models.get_strava_connection(rider_id)
    if existing:
        flash('Strava is already connected.', 'info')
        return redirect(url_for('auth.my_profile'))

    broker_secret = current_app.config.get('BROKER_HMAC_SECRET')
    if current_app.config.get('STRAVA_BROKER_ENABLED') and broker_secret:
        return_url = url_for('strava.broker_return', _external=True)
        state = sign_state(
            secret=broker_secret,
            origin=current_app.config['STRAVA_BROKER_ORIGIN'],
            ta_rider_id=rider_id,
            return_url=return_url,
        )
        params = {
            'origin': current_app.config['STRAVA_BROKER_ORIGIN'],
            'state': state,
        }
        broker_url = f"{current_app.config['STRAVA_BROKER_URL']}?{urlencode(params)}"
        return redirect(broker_url)

    # Direct flow (rollback path): store rider_id in session for the callback.
    session['strava_connecting_rider_id'] = rider_id

    params = {
        'client_id': current_app.config['STRAVA_CLIENT_ID'],
        'response_type': 'code',
        'redirect_uri': url_for('strava.callback', _external=True),
        'scope': current_app.config['STRAVA_SCOPE'],
        'approval_prompt': 'auto',
    }

    auth_url = f"{current_app.config['STRAVA_AUTH_URL']}?{urlencode(params)}"
    return redirect(auth_url)


@strava_bp.route('/broker-return')
@profile_required
def broker_return():
    """Consume the one-time broker handoff and store the Strava connection.

    BrevetHub bounces back here with an opaque ``?code=`` (or an ``?error=`` if the
    user denied / the broker failed). The code is consumed in a single atomic
    delete-returning step that enforces single-use and the handoff TTL; the row's
    ``ta_rider_id`` must equal the logged-in rider before any tokens are stored.
    """
    rider_id = session.get('rider_id')

    error = request.args.get('error')
    if error:
        flash('Strava authorization was denied or could not be completed.', 'error')
        current_app.logger.info("Strava broker return error for rider %s: %s", rider_id, error)
        return redirect(url_for('auth.my_profile'))

    code = request.args.get('code')
    if not code:
        flash('Strava connection could not be completed. Please try again.', 'error')
        return redirect(url_for('auth.my_profile'))

    handoff = models.consume_strava_broker_handoff(code)
    if not handoff:
        # Unknown, already consumed, or expired one-time code.
        flash('This Strava connection link has expired or was already used. Please try again.', 'error')
        current_app.logger.warning("Strava broker handoff not consumable for rider %s", rider_id)
        return redirect(url_for('auth.my_profile'))

    # The tokens must land on the rider who initiated the connect, not whoever
    # happens to hold this session — a hard reject on mismatch.
    if handoff['ta_rider_id'] != rider_id:
        flash('Strava connection could not be verified for your account.', 'error')
        current_app.logger.warning(
            "Strava broker handoff rider mismatch: handoff=%s session=%s",
            handoff['ta_rider_id'], rider_id,
        )
        return redirect(url_for('auth.my_profile'))

    strava_athlete_id = handoff.get('strava_athlete_id')
    if not strava_athlete_id:
        flash('Failed to get athlete info from Strava.', 'error')
        return redirect(url_for('auth.my_profile'))

    models.create_strava_connection(
        rider_id=rider_id,
        strava_athlete_id=strava_athlete_id,
        access_token=handoff['access_token'],
        refresh_token=handoff['refresh_token'],
        expires_at=handoff['expires_at'],
        scope=handoff.get('scope') or '',
    )

    _run_post_connect_sync(rider_id)
    return redirect(url_for('auth.my_profile'))


@strava_bp.route('/callback')
def callback():
    """Handle Strava OAuth callback."""
    rider_id = session.pop('strava_connecting_rider_id', None)
    if not rider_id:
        flash('Session expired. Please try connecting again.', 'error')
        return redirect(url_for('main.index'))

    error = request.args.get('error')
    if error:
        flash(f'Strava authorization was denied: {error}', 'error')
        return redirect(url_for('auth.my_profile'))

    code = request.args.get('code')
    scope = request.args.get('scope', '')

    if not code:
        flash('Missing authorization code from Strava.', 'error')
        return redirect(url_for('main.index'))

    try:
        token_data = exchange_code_for_token(code)

        athlete = token_data.get('athlete', {})
        strava_athlete_id = athlete.get('id')

        if not strava_athlete_id:
            flash('Failed to get athlete info from Strava.', 'error')
            return redirect(url_for('auth.my_profile'))

        # Store connection
        models.create_strava_connection(
            rider_id=rider_id,
            strava_athlete_id=strava_athlete_id,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_at=token_data['expires_at'],
            scope=scope,
        )

        # Initial sync — fetch 1 year of history
        try:
            counts = sync_rider_activities(rider_id, days=365)
            cache.clear()  # Clear cache after Strava sync
            flash(f'Strava connected! Synced {counts["new"]} new activities.', 'success')
        except Exception as e:
            flash('Strava connected, but activity sync failed. We will retry later.', 'warning')
            print(f"Strava initial sync error for rider {rider_id}: {e}")

    except Exception as e:
        flash(f'Failed to connect Strava: {str(e)}', 'error')
        print(f"Strava OAuth error: {e}")

    return redirect(url_for('auth.my_profile'))


@strava_bp.route('/sync')
@profile_required
def sync():
    """Manually trigger activity sync."""
    rider_id = session.get('rider_id')

    try:
        counts = sync_rider_activities(rider_id)
        cache.clear()  # Clear cache after Strava sync
        flash(f'Synced {counts["new"]} new, {counts["updated"]} updated activities from Strava.', 'success')
    except Exception as e:
        flash(f'Sync failed: {str(e)}', 'error')

    return redirect(url_for('auth.my_profile'))


@strava_bp.route('/disconnect', methods=['POST'])
@profile_required
def disconnect():
    """Disconnect Strava and delete stored data."""
    rider_id = session.get('rider_id')
    if request.form.get('confirm_delete') != 'DELETE':
        flash('Confirm permanent deletion before disconnecting Strava.',
              'warning')
        return redirect(url_for('auth.my_profile'))

    connection = models.get_strava_connection(rider_id)
    if connection:
        # Revocation is best-effort. Local deletion must always proceed so a
        # Strava outage cannot retain the rider's private data.
        try:
            deauthorize_strava(connection['access_token'])
        except Exception:
            current_app.logger.warning(
                'Remote Strava revocation failed for rider %s; proceeding '
                'with local deletion', rider_id)
        try:
            models.delete_strava_connection(rider_id)
        except Exception:
            current_app.logger.exception(
                'Local Strava deletion failed for rider %s', rider_id)
            flash(
                'Strava data could not be deleted right now. Nothing was '
                'partially removed; please try again.',
                'error',
            )
            return redirect(url_for('auth.my_profile'))
        cache.clear()  # Clear cache after Strava disconnect
        flash(
            'Strava disconnected. Tokens, imported activities, ride matches, '
            'and cached analyses were permanently deleted.',
            'success',
        )
    else:
        flash('No Strava connection found.', 'info')

    return redirect(url_for('auth.my_profile'))
