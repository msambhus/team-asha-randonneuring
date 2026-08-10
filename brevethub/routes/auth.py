"""BrevetHub authentication — Google OAuth via Authlib.

Reuses Team Asha's existing Google OAuth *client* (same GOOGLE_CLIENT_ID /
SECRET); the owner registers BrevetHub's own redirect URIs on that client. On
first sign-in a `rp_rider` row is created and the user is sent to /signup to add
an optional RUSA ID and pick a club; returning users with a completed profile go
straight to the dashboard.
"""
import os

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint, current_app, flash, redirect, render_template, request,
    session, url_for,
)

from brevethub import models
from brevethub.redirects import safe_redirect

auth_bp = Blueprint('auth', __name__)

# OAuth client, initialized against the app in the factory.
oauth = OAuth()


def google_oauth_redirect_uri():
    """Redirect URI sent to Google. Local dev uses localhost (not 127.0.0.1)."""
    override = current_app.config.get('GOOGLE_OAUTH_REDIRECT_URI')
    if override:
        return override
    if os.environ.get('VERCEL_ENV') != 'production':
        port = os.environ.get('PORT', '5001')
        return f'http://localhost:{port}/auth/google/callback'
    return url_for('auth.google_callback', _external=True)


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
    redirect_uri = google_oauth_redirect_uri()
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
    return safe_redirect(next_url, 'main.dashboard')


@auth_bp.route('/logout')
def logout():
    """Clear the session."""
    session.clear()
    return redirect(url_for('main.landing'))


@auth_bp.route('/my-profile')
def my_profile():
    """Compatibility endpoint for reused Team Asha profile/analysis templates."""
    return redirect(url_for('main.profile'))
