"""BrevetHub authentication — Google OAuth via Authlib.

Reuses Team Asha's existing Google OAuth *client* (same GOOGLE_CLIENT_ID /
SECRET); the owner registers BrevetHub's own redirect URIs on that client. On
first sign-in a `rp_rider` row is created and the user is sent to /signup to add
an optional RUSA ID and pick a club; returning users with a completed profile go
straight to the dashboard.
"""
from urllib.parse import urlparse

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint, current_app, flash, redirect, render_template, request,
    session, url_for,
)

from brevethub import models

auth_bp = Blueprint('auth', __name__)

# OAuth client, initialized against the app in the factory.
oauth = OAuth()


def init_oauth(app):
    """Register the Google provider on the BrevetHub app."""
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
        client_kwargs={'scope': 'openid email profile'},
    )


def _safe_redirect(url, fallback='main.dashboard'):
    """Redirect only to a relative path on this host (open-redirect guard)."""
    if url:
        parsed = urlparse(url)
        if not parsed.scheme and not parsed.netloc:
            return redirect(url)
    return redirect(url_for(fallback))


@auth_bp.route('/login')
def login():
    """Login page with the 'Sign in with Google' entry point."""
    if session.get('rider_id'):
        return redirect(url_for('main.dashboard'))
    next_url = request.args.get('next')
    if next_url:
        session['next_url'] = next_url
    return render_template('login.html')


@auth_bp.route('/google/login')
def google_login():
    """Kick off the Google OAuth redirect."""
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/google/callback')
def google_callback():
    """Handle the Google OAuth callback: upsert the rider, set the session, and
    route new riders to signup / returning riders to the dashboard."""
    try:
        token = oauth.google.authorize_access_token()
    except Exception as exc:  # noqa: BLE001 — any OAuth failure → back to login
        current_app.logger.warning("BrevetHub Google OAuth failed: %s", exc)
        flash('Login failed. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    user_info = token.get('userinfo') if token else None
    if not user_info:
        flash('Could not read your Google account info.', 'error')
        return redirect(url_for('auth.login'))

    google_id = user_info.get('sub')
    email = user_info.get('email')
    if not google_id or not email:
        flash('Google did not return the required account details.', 'error')
        return redirect(url_for('auth.login'))

    rider = models.get_rider_by_google_id(google_id)
    if not rider:
        rider = models.create_rider(email, google_id)
        current_app.logger.info("BrevetHub created rp_rider id=%s", rider['id'])
    else:
        models.update_rider_login(rider['id'])

    session.permanent = True
    session['rider_id'] = rider['id']
    session['email'] = rider['email']
    session['google_id'] = google_id

    if not rider['profile_completed']:
        return redirect(url_for('signup.signup'))

    next_url = session.pop('next_url', None)
    return _safe_redirect(next_url, fallback='main.dashboard')


@auth_bp.route('/logout')
def logout():
    """Clear the session."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.landing'))
