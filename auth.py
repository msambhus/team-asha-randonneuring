from functools import wraps
from flask import session, redirect, url_for, request, current_app


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def verify_password(password):
    return password == current_app.config['ADMIN_PASSWORD']
