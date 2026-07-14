"""BrevetHub Strava integration — OAuth connect, callback, disconnect, and the
per-rider activity summary shown on the dashboard.

Isolated from Team Asha: all Strava protocol work goes through the framework-free
``shared.strava`` layer (epoch-native tokens), tokens are stored only in the
``rp_strava_connection`` table via ``brevethub.models``, and fitness scoring
reuses ``shared.fitness``. Nothing here imports Team Asha code.

Security: the connect flow mints a per-flow CSRF ``state`` token, stores it in the
session, echoes it to Strava, and the callback validates it with a constant-time
compare BEFORE any code exchange or DB write. The state + the connecting rider id
are cleared from the session on every terminal path, so a token is single-use and
no stale linking session lingers. This deliberately hardens past Team Asha's
state-less flow (a called-out web-parity improvement, not a silent divergence).
"""
import hmac
import secrets
import time
from urllib.parse import urlencode

from flask import (Blueprint, current_app, flash, redirect, request, session,
                   url_for)

from brevethub import models
from brevethub.decorators import profile_required
from shared.strava import (deauthorize_strava, exchange_code_for_token,
                           fetch_activities, refresh_access_token,
                           summarize_activities, transform_activity)
from shared.fitness import calculate_fitness_score

strava_bp = Blueprint('strava', __name__)

# Cache the computed 28-day activity summary for 6 hours (matches Team Asha), so
# repeated dashboard loads do not re-hit the Strava API.
STRAVA_STATS_TTL = 6 * 3600
STRAVA_STATS_WINDOW = 28 * 24 * 3600  # 28-day activity window


@strava_bp.route('/connect')
@profile_required
def connect():
    """Begin the Strava OAuth flow: mint a CSRF state token and redirect to Strava."""
    rider_id = session.get('rider_id')

    if models.get_strava_connection(rider_id):
        flash('Strava is already connected.', 'info')
        return redirect(url_for('main.dashboard'))

    if not current_app.config.get('STRAVA_CLIENT_SECRET'):
        flash('Strava is not configured yet. Please try again later.', 'error')
        return redirect(url_for('main.dashboard'))

    state = secrets.token_urlsafe(32)
    session['strava_oauth_state'] = state
    session['strava_connecting_rider_id'] = rider_id

    params = {
        'client_id': current_app.config['STRAVA_CLIENT_ID'],
        'response_type': 'code',
        'redirect_uri': url_for('strava.callback', _external=True),
        'scope': current_app.config['STRAVA_SCOPE'],
        'approval_prompt': 'auto',
        'state': state,
    }
    auth_url = f"{current_app.config['STRAVA_AUTH_URL']}?{urlencode(params)}"
    return redirect(auth_url)


@strava_bp.route('/callback')
def callback():
    """Handle the Strava OAuth callback with strict CSRF-state validation."""
    # Pop the session state + rider id first, so they are cleared on EVERY path.
    session_state = session.pop('strava_oauth_state', None)
    rider_id = session.pop('strava_connecting_rider_id', None)
    query_state = request.args.get('state')

    # Validate the CSRF state before touching any code / DB. A missing session
    # state, missing query state, or mismatch is a hard reject.
    if (not session_state or not query_state
            or not hmac.compare_digest(session_state, query_state)):
        flash('Strava authorization could not be verified. Please try connecting again.', 'error')
        return redirect(url_for('main.dashboard'))

    if not rider_id:
        flash('Session expired. Please try connecting again.', 'error')
        return redirect(url_for('main.dashboard'))

    error = request.args.get('error')
    if error:
        flash(f'Strava authorization was denied: {error}', 'error')
        return redirect(url_for('main.dashboard'))

    code = request.args.get('code')
    scope = request.args.get('scope', '')
    if not code:
        flash('Missing authorization code from Strava.', 'error')
        return redirect(url_for('main.dashboard'))

    try:
        token_data = exchange_code_for_token(
            code,
            client_id=current_app.config['STRAVA_CLIENT_ID'],
            client_secret=current_app.config['STRAVA_CLIENT_SECRET'],
            token_url=current_app.config['STRAVA_TOKEN_URL'],
        )
        athlete = token_data.get('athlete', {}) or {}
        strava_athlete_id = athlete.get('id')
        if not strava_athlete_id:
            flash('Could not read athlete info from Strava. Please try again.', 'error')
            return redirect(url_for('main.dashboard'))

        models.upsert_strava_connection(
            rider_id,
            strava_athlete_id=strava_athlete_id,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_at=token_data['expires_at'],  # epoch int; model converts to TIMESTAMPTZ
            scope=scope,
        )
        flash('Strava connected!', 'success')
    except Exception as e:
        current_app.logger.warning('Strava OAuth error for rider %s: %s', rider_id, e)
        flash('Failed to connect Strava. Please try again.', 'error')

    return redirect(url_for('main.dashboard'))


@strava_bp.route('/disconnect', methods=['POST'])
@profile_required
def disconnect():
    """Disconnect Strava: revoke the token (best-effort) and delete the row."""
    rider_id = session.get('rider_id')

    connection = models.get_strava_connection(rider_id)
    if connection:
        if connection.get('access_token'):
            deauthorize_strava(connection['access_token'])
        models.delete_strava_connection(rider_id)
        flash('Strava has been disconnected.', 'success')
    else:
        flash('No Strava connection found.', 'info')

    return redirect(url_for('main.dashboard'))


# --------------------------------------------------------------------------- #
# Dashboard data assembly (imported by routes/main.py). Cache-aware and
# failure-tolerant: a Strava outage degrades to the cached summary (or empty)
# with a message — never a 500.
# --------------------------------------------------------------------------- #
def _valid_access_token(rider_id, connection):
    """Return a usable access token, refreshing (and persisting) if near-expiry.

    ``connection['expires_at']`` is an epoch float (the model getter converts it),
    so the staleness check stays a plain numeric compare against ``time.time()``.
    """
    expires_at = connection.get('expires_at')
    if expires_at and expires_at > time.time() + 60:  # 60s buffer
        return connection['access_token']

    token_data = refresh_access_token(
        connection['refresh_token'],
        client_id=current_app.config['STRAVA_CLIENT_ID'],
        client_secret=current_app.config['STRAVA_CLIENT_SECRET'],
        token_url=current_app.config['STRAVA_TOKEN_URL'],
    )
    models.update_strava_tokens(
        rider_id,
        access_token=token_data['access_token'],
        refresh_token=token_data['refresh_token'],
        expires_at=token_data['expires_at'],
    )
    return token_data['access_token']


def _compute_strava_stats(rider_id, connection):
    """Fetch the last 28 days of activities, summarize them, and score fitness."""
    token = _valid_access_token(rider_id, connection)
    after_epoch = int(time.time()) - STRAVA_STATS_WINDOW
    raw = fetch_activities(
        token,
        api_base=current_app.config['STRAVA_API_BASE'],
        after_epoch=after_epoch,
    )
    activities = [transform_activity(a, rider_id) for a in raw]
    summary = summarize_activities(activities)
    fitness = calculate_fitness_score(activities)
    summary['fitness'] = fitness['total'] if fitness else None
    return summary


def load_strava_section(rider):
    """Assemble the dashboard Strava section for a rider (cache-aware, failure-tolerant).

    Returns a dict the template renders directly:
      - not connected:  {'connected': False}
      - connected:      {'connected': True, 'athlete_id', 'stats', 'error'}
    """
    connection = models.get_strava_connection(rider['id'])
    if not connection:
        return {'connected': False}

    stats = connection.get('stats_cache')
    fetched_at = connection.get('stats_fetched_at')  # epoch float or None
    fresh = fetched_at is not None and (time.time() - fetched_at) < STRAVA_STATS_TTL

    error = None
    if not fresh or stats is None:
        try:
            stats = _compute_strava_stats(rider['id'], connection)
            models.update_strava_stats(rider['id'], stats)
        except Exception as e:
            current_app.logger.warning('Strava stats fetch failed for rider %s: %s', rider['id'], e)
            error = 'Could not refresh Strava activity right now.'

    return {
        'connected': True,
        'athlete_id': connection.get('strava_athlete_id'),
        'stats': stats,
        'error': error,
    }
