"""BrevetHub auth decorators + the current-rider helper.

Session shape: `rider_id` is set on Google callback. A rider whose profile is
incomplete (no club chosen yet) is bounced to /signup; a fully-authenticated
rider reaches the dashboard.
"""
from functools import wraps

from flask import redirect, request, session, url_for

from brevethub import models


def current_rider():
    """Return the logged-in rp_rider row, or None if not signed in."""
    rider_id = session.get('rider_id')
    if not rider_id:
        return None
    return models.get_rider_by_id(rider_id)


def login_required(f):
    """Require a signed-in rider; otherwise send to the login page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('rider_id'):
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def profile_required(f):
    """Require a signed-in rider who has completed signup (chosen a club)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        rider = current_rider()
        if not rider:
            return redirect(url_for('auth.login', next=request.path))
        if not rider['profile_completed']:
            return redirect(url_for('signup.signup'))
        return f(*args, **kwargs)
    return decorated
