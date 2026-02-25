"""Strava integration routes: OAuth connect, callback, sync, disconnect."""
from flask import (Blueprint, redirect, url_for, session, flash,
                   request, current_app)
from auth import profile_required
from urllib.parse import urlencode
import models
from services.strava import exchange_code_for_token, sync_rider_activities, deauthorize_strava

strava_bp = Blueprint('strava', __name__)


@strava_bp.route('/connect')
@profile_required
def connect():
    """Redirect to Strava authorization page."""
    rider_id = session.get('rider_id')

    # Check if already connected
    existing = models.get_strava_connection(rider_id)
    if existing:
        flash('Strava is already connected.', 'info')
        return redirect(url_for('auth.my_profile'))

    # Store rider_id in session for callback
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
            count = sync_rider_activities(rider_id, days=365)
            flash(f'Strava connected! Synced {count} activities.', 'success')
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
        count = sync_rider_activities(rider_id)
        flash(f'Synced {count} activities from Strava.', 'success')
    except Exception as e:
        flash(f'Sync failed: {str(e)}', 'error')

    return redirect(url_for('auth.my_profile'))


@strava_bp.route('/disconnect', methods=['POST'])
@profile_required
def disconnect():
    """Disconnect Strava and delete stored data."""
    rider_id = session.get('rider_id')

    connection = models.get_strava_connection(rider_id)
    if connection:
        # Revoke token at Strava
        deauthorize_strava(connection['access_token'])
        # Delete from DB
        models.delete_strava_connection(rider_id)
        flash('Strava has been disconnected.', 'success')
    else:
        flash('No Strava connection found.', 'info')

    return redirect(url_for('auth.my_profile'))
