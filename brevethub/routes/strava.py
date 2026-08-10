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
from datetime import datetime
from urllib.parse import urlencode

from flask import (Blueprint, abort, current_app, flash, redirect, request,
                   session, url_for)

from brevethub import models
from brevethub.decorators import profile_required
from brevethub.redirects import is_allowed_broker_return_url
from shared.broker_state import verify_state
from shared.strava import (deauthorize_strava, exchange_code_for_token,
                           fetch_activities, refresh_access_token,
                           summarize_activities, transform_activity)
from shared.fitness import calculate_fitness_score
from shared.eddington import calculate_eddington_number

strava_bp = Blueprint('strava', __name__)

# Cache the computed 28-day activity summary for 6 hours (matches Team Asha), so
# repeated dashboard loads do not re-hit the Strava API.
STRAVA_STATS_TTL = 6 * 3600
STRAVA_STATS_WINDOW = 28 * 24 * 3600  # 28-day activity window
STRAVA_EVIDENCE_WINDOW = 365 * 24 * 3600  # evidence picker needs completed brevets too

# The cycling Eddington is a LIFETIME stat, so its compute fetches the rider full
# Strava history (not the 28-day dashboard window). ``after=0`` (the Unix epoch)
# asks Strava for every activity, effectively all-time; the window is bounded only
# by shared.strava.fetch_activities pagination and can be widened later without a
# schema change. Compute runs OFF the request path (Strava connect + the daily
# refresh cron), so this full fetch is never on a page load.
EDDINGTON_HISTORY_AFTER_EPOCH = 0


@strava_bp.route('/connect')
def connect():
    """Begin a Strava OAuth flow for either a BrevetHub rider or a Team Asha origin.

    A request carrying an ``origin`` query param is a Team-Asha-brokered connect
    (no BrevetHub login); everything else is a logged-in BrevetHub rider. The two
    origins never share state: the rider path keeps its session-CSRF token, the
    Team Asha path is authenticated by an HMAC-signed state instead.
    """
    origin = request.args.get('origin')
    if origin is not None:
        return _broker_connect(origin)
    return _rider_connect()


@profile_required
def _rider_connect():
    """BrevetHub rider connect: mint a CSRF state token and redirect to Strava."""
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


def _broker_connect(origin):
    """Team-Asha-brokered connect: authenticate the signed state, claim its nonce
    for single use, then redirect to Strava with the state echoed through.

    Order matters — verify signature/TTL, check the return-URL allowlist, and only
    THEN burn the single-use nonce, so a forged / expired / disallowed request can
    never consume a nonce or reach Strava. Every reject is a hard 4xx with a
    reason logged (never the state blob) and no Strava redirect.
    """
    secret = current_app.config.get('BROKER_HMAC_SECRET')
    if origin != current_app.config['BROKER_TEAM_ASHA_ORIGIN']:
        current_app.logger.warning('Strava broker connect: unknown origin %r', origin)
        abort(400)
    if not secret:
        current_app.logger.warning('Strava broker connect: BROKER_HMAC_SECRET not configured')
        abort(503)

    state = request.args.get('state')
    payload = verify_state(
        state, secret=secret, max_age=current_app.config['BROKER_STATE_MAX_AGE']
    ) if state else None
    if payload is None or payload.get('origin') != origin:
        current_app.logger.warning('Strava broker connect: state failed verification')
        abort(400)

    return_url = payload.get('return_url')
    if not is_allowed_broker_return_url(
            return_url, current_app.config['BROKER_RETURN_URL_ALLOWLIST']):
        current_app.logger.warning('Strava broker connect: return_url not allowlisted')
        abort(400)

    # Durable single-use claim (replay guard) — after signature + allowlist so a
    # bad request never burns a nonce. Zero rows => the nonce was already claimed.
    if models.claim_broker_state(
            payload['nonce'],
            state_ttl_seconds=current_app.config['BROKER_STATE_MAX_AGE']) is None:
        current_app.logger.warning('Strava broker connect: state replay rejected')
        abort(409)

    if not current_app.config.get('STRAVA_CLIENT_SECRET'):
        current_app.logger.warning('Strava broker connect: STRAVA_CLIENT_SECRET not configured')
        abort(503)

    session['strava_broker_flow'] = True  # single-use context, cleared at callback
    params = {
        'client_id': current_app.config['STRAVA_CLIENT_ID'],
        'response_type': 'code',
        'redirect_uri': url_for('strava.callback', _external=True),
        'scope': current_app.config['STRAVA_SCOPE'],
        'approval_prompt': 'auto',
        'state': state,  # echoed to Strava so /callback can re-verify it
    }
    auth_url = f"{current_app.config['STRAVA_AUTH_URL']}?{urlencode(params)}"
    return redirect(auth_url)


@strava_bp.route('/callback')
def callback():
    """Handle the Strava OAuth callback for both a BrevetHub rider and a Team Asha
    broker flow.

    A ``state`` that verifies as a signed broker state (Team Asha origin) routes to
    the broker handler; everything else is the BrevetHub-rider session-CSRF flow.
    """
    query_state = request.args.get('state')
    secret = current_app.config.get('BROKER_HMAC_SECRET')
    broker_payload = None
    if secret and query_state:
        broker_payload = verify_state(
            query_state, secret=secret,
            max_age=current_app.config['BROKER_STATE_MAX_AGE'],
        )
    if (broker_payload is not None
            and broker_payload.get('origin') == current_app.config['BROKER_TEAM_ASHA_ORIGIN']):
        return _broker_callback(broker_payload)

    # Pop the session state + rider id first, so they are cleared on EVERY path.
    session_state = session.pop('strava_oauth_state', None)
    rider_id = session.pop('strava_connecting_rider_id', None)

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
        # The Eddington is computed OFF the request path by the daily
        # /cron/refresh-eddington, never here. An all-time Strava history fetch on
        # the connect redirect can exceed the serverless function timeout for an
        # active rider (a platform kill the fail-soft try cannot catch, since
        # fetch_activities paginates the full history with no page cap). The
        # profile shows a "will appear after the next sync" note until the cron
        # fills the value in.
    except Exception as e:
        current_app.logger.warning('Strava OAuth error for rider %s: %s', rider_id, e)
        flash('Failed to connect Strava. Please try again.', 'error')

    return redirect(url_for('main.dashboard'))


def _broker_return_redirect(return_url, **params):
    """302 back to a Team Asha return URL with a neutral query param.

    Only ever the opaque handoff ``code`` or a neutral ``error`` is passed — never
    a Strava token — so no token can leak into a URL, query string, or log line.
    """
    return redirect(f"{return_url}?{urlencode(params)}")


def _broker_callback(payload):
    """Team Asha broker callback: exchange the Strava code, stash the tokens in a
    one-time handoff row, and 302 back to Team Asha with only the opaque code.

    The tokens are written to ``rp_strava_broker_handoff`` (BrevetHub-owned) and
    NEVER to ``rp_strava_connection``; Team Asha consumes the handoff at its
    ``/strava/broker-return``. The return URL is re-checked against the allowlist
    here (defense in depth) before any bounce.
    """
    session.pop('strava_broker_flow', None)  # single-use context cleared

    return_url = payload.get('return_url')
    ta_rider_id = payload.get('ta_rider_id')
    # Defense in depth: never bounce to a non-allowlisted host, even from a state
    # that passed the HMAC check.
    if not is_allowed_broker_return_url(
            return_url, current_app.config['BROKER_RETURN_URL_ALLOWLIST']):
        current_app.logger.warning('Strava broker callback: return_url not allowlisted')
        abort(400)
    if ta_rider_id is None:
        current_app.logger.warning('Strava broker callback: state missing ta_rider_id')
        return _broker_return_redirect(return_url, error='connect_failed')

    # Enforce the durable single-use claim made at /connect. A signed state that
    # skipped /connect (a direct-to-Strava bypass) or is being replayed through
    # /callback has no consumable claim → hard reject before any token exchange.
    # This runs for EVERY terminal path (including denial) so the nonce burns once.
    if models.consume_broker_state(payload['nonce']) is None:
        current_app.logger.warning('Strava broker callback: state not claimed or already used')
        abort(409)

    error = request.args.get('error')
    if error:
        current_app.logger.info('Strava broker callback: authorization denied/aborted')
        return _broker_return_redirect(return_url, error='access_denied')

    code = request.args.get('code')
    if not code:
        return _broker_return_redirect(return_url, error='missing_code')

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
            return _broker_return_redirect(return_url, error='connect_failed')

        handoff_code = models.create_broker_handoff(
            ta_rider_id=ta_rider_id,
            strava_athlete_id=strava_athlete_id,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            strava_token_expires_at=token_data['expires_at'],  # epoch int
            scope=request.args.get('scope', ''),
            handoff_ttl_seconds=current_app.config['BROKER_HANDOFF_TTL'],
        )
    except Exception as e:
        current_app.logger.warning('Strava broker callback exchange error: %s', e)
        return _broker_return_redirect(return_url, error='connect_failed')

    return _broker_return_redirect(return_url, code=handoff_code)


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
    """Fetch one year of activities, summarizing recent training and retaining a
    broader outdoor-ride list for brevet evidence matching."""
    token = _valid_access_token(rider_id, connection)
    after_epoch = int(time.time()) - STRAVA_EVIDENCE_WINDOW
    raw = fetch_activities(
        token,
        api_base=current_app.config['STRAVA_API_BASE'],
        after_epoch=after_epoch,
    )
    activities = [transform_activity(a, rider_id) for a in raw]
    recent_cutoff = time.time() - STRAVA_STATS_WINDOW
    recent = [a for a in activities if _activity_epoch(a) >= recent_cutoff]
    summary = summarize_activities(recent)
    fitness = calculate_fitness_score(recent)
    summary['fitness'] = fitness['total'] if fitness else None
    # Keep a compact, presentation-safe recent feed in the same cache so My
    # Rides never makes a second Strava API request during page rendering.
    summary['activities'] = [{
        'strava_activity_id': activity.get('strava_activity_id'),
        'name': activity.get('name'),
        'start_date_local': activity.get('start_date_local'),
        'distance': activity.get('distance'),
        'elapsed_time': activity.get('elapsed_time'),
        'moving_time': activity.get('moving_time'),
        'total_elevation_gain': activity.get('total_elevation_gain'),
        'activity_type': activity.get('activity_type'),
    } for activity in recent[:50]]
    summary['evidence_activities'] = [{
        'strava_activity_id': activity.get('strava_activity_id'),
        'name': activity.get('name'),
        'start_date': activity.get('start_date'),
        'start_date_local': activity.get('start_date_local'),
        'distance': activity.get('distance'),
        'activity_type': activity.get('activity_type'),
    } for activity in activities if activity.get('activity_type') in ('Ride', 'EBikeRide')][:200]
    return summary


def _activity_epoch(activity):
    value = activity.get('start_date') or activity.get('start_date_local')
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


def compute_and_cache_eddington(rider_id, connection):
    """Compute the rider all-time cycling Eddington (miles + km) and cache it.

    Owner-context ONLY: uses the rider own live token to fetch their own full Strava
    history, transforms each raw activity (raw ``type`` -> the engine's
    ``activity_type`` filter key, via shared.strava.transform_activity), computes E
    with the shared engine in both units, and persists them through
    models.set_rider_eddington. Never called on a public-profile view (a public
    viewer holds no token) — only on the Strava connect callback and the daily
    /cron/refresh-eddington. Returns the computed ``{eddington_km, eddington_miles}``.
    """
    token = _valid_access_token(rider_id, connection)
    raw = fetch_activities(
        token,
        api_base=current_app.config['STRAVA_API_BASE'],
        after_epoch=EDDINGTON_HISTORY_AFTER_EPOCH,
    )
    # Transform first: the engine filters on ``activity_type``, but raw Strava
    # activities carry ``type`` — feeding raw dicts would silently yield E=0.
    activities = [transform_activity(a, rider_id) for a in raw]
    eddington_miles = calculate_eddington_number(activities, unit='miles')
    eddington_km = calculate_eddington_number(activities, unit='km')
    models.set_rider_eddington(
        rider_id, eddington_km=eddington_km, eddington_miles=eddington_miles)
    return {'eddington_km': eddington_km, 'eddington_miles': eddington_miles}


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
    # The activity type is needed by evidence selection to exclude runs and
    # virtual workouts; invalidate pre-type caches once so the outdoor list is
    # accurate without waiting six hours.
    needs_activity_types = bool(stats and (
        any('activity_type' not in activity for activity in (stats.get('activities') or []))
        or 'evidence_activities' not in stats
        or any('start_date' not in activity for activity in (stats.get('evidence_activities') or []))))

    error = None
    if not fresh or stats is None or needs_activity_types:
        try:
            stats = _compute_strava_stats(rider['id'], connection)
            models.update_strava_stats(rider['id'], stats)
        except Exception as e:
            current_app.logger.warning('Strava stats fetch failed for rider %s: %s', rider['id'], e)
            error = 'Could not refresh Strava activity right now.'

    eddington_miles = rider.get('eddington_miles')
    eddington_km = rider.get('eddington_km')
    if eddington_miles is None:
        try:
            computed = compute_and_cache_eddington(rider['id'], connection)
            eddington_miles = computed['eddington_miles']
            eddington_km = computed['eddington_km']
        except Exception as e:
            current_app.logger.warning(
                'Eddington compute failed for rider %s: %s', rider['id'], e)

    return {
        'connected': True,
        'athlete_id': connection.get('strava_athlete_id'),
        'stats': stats,
        'error': error,
        'eddington_miles': eddington_miles,
        'eddington_km': eddington_km,
    }
