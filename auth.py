from functools import wraps
from flask import session, redirect, url_for, request, current_app, flash


def login_required(f):
    """Require user to be logged in (for admin routes)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def user_login_required(f):
    """Require user authentication via Google OAuth."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def profile_required(f):
    """Require user to have completed profile setup."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('auth.login', next=request.path))
        
        if not session.get('rider_id'):
            flash('Please complete your profile setup', 'warning')
            return redirect(url_for('auth.setup_profile'))
        
        return f(*args, **kwargs)
    return decorated


def verify_password(password):
    """Verify admin password."""
    return password == current_app.config['ADMIN_PASSWORD']
