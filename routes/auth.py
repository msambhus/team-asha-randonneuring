"""Authentication routes - Google OAuth login and profile setup."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from authlib.integrations.flask_client import OAuth
from werkzeug.security import gen_salt
import models
from utils.rusa_validator import validate_rusa_id, get_rusa_info

auth_bp = Blueprint('auth', __name__)

# OAuth will be initialized in the app factory
oauth = OAuth()


def init_oauth(app):
    """Initialize OAuth with Flask app."""
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
        client_kwargs={
            'scope': 'openid email profile'
        }
    )


@auth_bp.route('/login')
def login():
    """Display login page."""
    # If already logged in, redirect to home
    if session.get('user_id'):
        return redirect(url_for('main.index'))
    return render_template('login.html')


@auth_bp.route('/google/login')
def google_login():
    """Initiate Google OAuth login."""
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/google/callback')
def google_callback():
    """Handle Google OAuth callback."""
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if not user_info:
            flash('Failed to get user info from Google', 'error')
            return redirect(url_for('auth.login'))
        
        google_id = user_info.get('sub')
        email = user_info.get('email')
        
        if not google_id or not email:
            flash('Missing required information from Google', 'error')
            return redirect(url_for('auth.login'))
        
        # Check if user exists
        user = models.get_user_by_google_id(google_id)
        
        if not user:
            # Create new user
            user = models.create_user(email, google_id)
            if not user:
                flash('Failed to create user account', 'error')
                return redirect(url_for('auth.login'))
        else:
            # Update last login time
            models.update_user_login_time(user['id'])
            user = models.get_user_by_id(user['id'])
        
        # Set session
        session['user_id'] = user['id']
        session['email'] = user['email']
        session['google_id'] = user['google_id']
        
        # If profile not completed, redirect to profile setup
        if not user['profile_completed']:
            return redirect(url_for('auth.setup_profile'))
        
        # Store rider_id in session for convenience
        if user['rider_id']:
            session['rider_id'] = user['rider_id']
            rider = models.get_rider_by_rusa(
                models._execute("SELECT rusa_id FROM rider WHERE id = %s", 
                               (user['rider_id'],)).fetchone()['rusa_id']
            )
            session['rider_name'] = f"{rider['first_name']} {rider['last_name']}"
        
        flash('Successfully logged in!', 'success')
        return redirect(url_for('main.index'))
        
    except Exception as e:
        flash(f'Login failed: {str(e)}', 'error')
        return redirect(url_for('auth.login'))


@auth_bp.route('/validate-rusa-id/<int:rusa_id>')
def validate_rusa_id_api(rusa_id):
    """API endpoint to fetch RUSA information by ID."""
    if not session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Check if RUSA ID exists and if it's already linked to a user
    existing_rider = models.get_rider_by_rusa_id(rusa_id)
    if existing_rider:
        # Check if this rider is already linked to a user account
        linked_user = models.is_rider_linked_to_user(existing_rider['id'])
        if linked_user:
            return jsonify({
                'valid': False,
                'error': 'This RUSA ID is already registered by another user'
            }), 400
        # Rider exists but not linked - will be claimed by this user
    
    # Fetch info from RUSA.org
    info = get_rusa_info(rusa_id)
    
    if info['valid']:
        return jsonify({
            'valid': True,
            'first_name': info['first_name'],
            'last_name': info['last_name'],
            'full_name': info['rusa_name'],
            'club': info['rusa_club']
        })
    else:
        return jsonify({
            'valid': False,
            'error': info['error']
        }), 404


@auth_bp.route('/setup-profile', methods=['GET', 'POST'])
def setup_profile():
    """Profile setup page for first-time users."""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    
    user = models.get_user_by_id(session['user_id'])
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('auth.login'))
    
    # If profile already completed, redirect to home
    if user['profile_completed']:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        rusa_id = request.form.get('rusa_id', '').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        
        # Validate input
        if not rusa_id:
            flash('RUSA ID is required', 'error')
            return render_template('setup_profile.html', rusa_id=rusa_id)
        
        try:
            rusa_id = int(rusa_id)
        except ValueError:
            flash('RUSA ID must be a number', 'error')
            return render_template('setup_profile.html', rusa_id=rusa_id)
        
        # Names should have been fetched automatically, but validate them
        if not first_name or not last_name:
            flash('Unable to retrieve rider information. Please try again.', 'error')
            return render_template('setup_profile.html', rusa_id=rusa_id)
        
        # Check if RUSA ID exists and if it's already linked to another user
        existing_rider = models.get_rider_by_rusa_id(rusa_id)
        if existing_rider:
            # Check if this rider is already linked to a user account
            linked_user = models.is_rider_linked_to_user(existing_rider['id'])
            if linked_user:
                flash('This RUSA ID is already registered by another user', 'error')
                return render_template('setup_profile.html', rusa_id=rusa_id)
            # Rider exists but not linked - we'll use this existing rider
        
        # Validate with RUSA website one final time
        validation = validate_rusa_id(rusa_id, first_name, last_name)
        
        if not validation['valid']:
            flash(validation['error'], 'error')
            return render_template('setup_profile.html', rusa_id=rusa_id)
        
        # Use existing rider or create new one
        rider = existing_rider or models.get_rider_by_name_and_rusa(first_name, last_name, rusa_id)
        
        if not rider:
            # Create new rider
            rider = models.create_rider(first_name, last_name, rusa_id)
            if not rider:
                flash('Failed to create rider profile', 'error')
                return render_template('setup_profile.html', rusa_id=rusa_id)
        
        # Link user to rider
        success = models.complete_user_profile(user['id'], rider['id'])
        if not success:
            flash('Failed to complete profile setup', 'error')
            return render_template('setup_profile.html', rusa_id=rusa_id)
        
        # Update session
        session['rider_id'] = rider['id']
        session['rider_name'] = f"{rider['first_name']} {rider['last_name']}"
        
        flash(f'Welcome, {rider["first_name"]}! Your profile has been set up successfully.', 'success')
        return redirect(url_for('main.index'))
    
    return render_template('setup_profile.html')


@auth_bp.route('/logout')
def logout():
    """Log out the current user."""
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('main.index'))
