"""Authentication routes - Google OAuth login and profile setup."""
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from authlib.integrations.flask_client import OAuth
from werkzeug.security import gen_salt
from werkzeug.utils import redirect as werkzeug_redirect
import models
from utils.rusa_validator import validate_rusa_id, get_rusa_info

auth_bp = Blueprint('auth', __name__)

# OAuth will be initialized in the app factory
oauth = OAuth()


def _safe_redirect(url, fallback='main.index'):
    """Redirect to `url` only if it is a relative path on this host.

    Prevents open-redirect attacks where an attacker sets
    `?next=https://evil.com` and the app blindly redirects there.
    """
    from urllib.parse import urlparse
    if url:
        parsed = urlparse(url)
        # Allow only relative paths (no scheme, no netloc)
        if not parsed.scheme and not parsed.netloc:
            return redirect(url)
    return redirect(url_for(fallback))


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
    
    # Store the next URL if provided
    next_url = request.args.get('next')
    if next_url:
        session['next_url'] = next_url
    
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
        
        # Set session. permanent + PERMANENT_SESSION_LIFETIME (config, 30d) make
        # this a persistent cookie so mobile browsers/PWAs don't drop the login
        # on backgrounding — the cause of frequent "session timed out" logouts.
        session.permanent = True
        session['user_id'] = user['id']
        session['email'] = user['email']
        session['google_id'] = user['google_id']
        
        # If profile not completed, redirect to profile setup
        if not user['profile_completed']:
            return redirect(url_for('auth.setup_profile'))
        
        # Store rider info in session for convenience
        if user['rider_id']:
            session['rider_id'] = user['rider_id']
            try:
                rusa_row = models._execute(
                    "SELECT rusa_id, first_name, last_name FROM rider WHERE id = %s",
                    (user['rider_id'],)
                ).fetchone()
                if rusa_row:
                    session['rider_name'] = f"{rusa_row['first_name']} {rusa_row['last_name']}"
                    if rusa_row['rusa_id']:
                        session['rider_rusa_id'] = rusa_row['rusa_id']
            except Exception:
                pass  # rider_rusa_id will be backfilled by the context processor
        
        flash('Successfully logged in!', 'success')
        
        # Redirect to stored next URL or home
        next_url = session.pop('next_url', None)
        return _safe_redirect(next_url, fallback='main.index')
        
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
        if rider.get('rusa_id'):
            session['rider_rusa_id'] = rider['rusa_id']
        
        flash(f'Welcome, {rider["first_name"]}! Your profile has been set up successfully.', 'success')
        
        # Redirect to stored next URL or home
        next_url = session.pop('next_url', None)
        return _safe_redirect(next_url, fallback='main.index')
    
    return render_template('setup_profile.html')


@auth_bp.route('/my-profile')
def my_profile():
    """My Profile page — shows name, email, photo, Strava integration, fitness score."""
    if not session.get('user_id'):
        flash('Please log in to view your profile.', 'warning')
        return redirect(url_for('auth.login', next=request.path))

    rider_id = session.get('rider_id')
    if not rider_id:
        flash('Please complete your profile setup first.', 'warning')
        return redirect(url_for('auth.setup_profile'))

    # Get rider info (with profile data — photo, bio, PBP, strava privacy)
    rider_row = models._execute("""
        SELECT r.*, rp.photo_filename, rp.bio, rp.pbp_2023_registered, rp.pbp_2023_status, rp.strava_data_private
        FROM rider r LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        WHERE r.id = %s
    """, (rider_id,)).fetchone()

    if not rider_row:
        flash('Rider not found.', 'error')
        return redirect(url_for('main.index'))

    rider = dict(rider_row)

    # Career stats
    career = models.get_rider_career_stats(rider_id)

    # Total SR count
    total_srs = models.get_rider_total_srs(rider_id)

    # Strava integration
    strava_connection = models.get_strava_connection(rider_id)
    eddington_data = None
    if strava_connection and strava_connection.get('eddington_number_miles'):
        from services.eddington import get_eddington_badge_level
        eddington_miles = int(strava_connection.get('eddington_number_miles') or 0)
        eddington_data = {
            'miles': eddington_miles,
            'km': int(strava_connection.get('eddington_number_km') or 0),
            'badge': get_eddington_badge_level(eddington_miles),
        }
    # Feature stays dormant until its dedicated encryption key is configured.
    # This also makes the code-safe deployment precede migration 057/key rollout
    # without My Profile querying a table that may not exist yet.
    garmin_connection = (
        models.get_garmin_connection(rider_id)
        if current_app.config.get('GARMIN_TOKEN_ENCRYPTION_KEY') else None
    )
    garmin_snapshot = (
        models.get_latest_garmin_performance_snapshot(rider_id)
        if garmin_connection else None
    )
    garmin_history = (
        models.get_garmin_performance_history_summary(rider_id)
        if garmin_connection else None
    )
    sram_axs_connection = (
        models.get_sram_axs_connection(rider_id)
        if current_app.config.get('SRAM_AXS_TOKEN_ENCRYPTION_KEY') else None
    )
    strava_activities = []
    fitness_score = None

    if strava_connection:
        # Auto-sync if stale (> 6 hours since last sync)
        import time
        last_sync = strava_connection.get('last_sync_at')
        if not last_sync or (time.time() - last_sync.timestamp()) > 6 * 3600:
            try:
                from services.strava import sync_rider_activities
                sync_rider_activities(rider_id)
                strava_connection = models.get_strava_connection(rider_id)
            except Exception:
                pass  # Silent failure — stale data is better than no page

        strava_activities = models.get_strava_activities_for_calendar(rider_id, days=28)
        if strava_activities:
            from services.fitness import calculate_fitness_score
            fitness_score = calculate_fitness_score(strava_activities)

    return render_template('my_profile.html',
                           rider=rider,
                           email=session.get('email'),
                           career_rides=career['total_rides'],
                           career_kms=career['total_kms'],
                           total_srs=total_srs,
                           eddington_data=eddington_data,
                           strava_connection=strava_connection,
                           garmin_connection=garmin_connection,
                           garmin_snapshot=garmin_snapshot,
                           garmin_history=garmin_history,
                           sram_axs_connection=sram_axs_connection,
                           strava_activities=strava_activities,
                           fitness_score=fitness_score)


@auth_bp.route('/my-rides')
def my_rides():
    """Private activity dashboard for the signed-in rider.

    Provider activities are owner-scoped in the model queries, and explicit
    Garmin/Strava matches collapse into one logical ride card.
    """
    if not session.get('user_id'):
        flash('Please log in to view your rides.', 'warning')
        return redirect(url_for('auth.login', next=request.path))

    rider_id = session.get('rider_id')
    if not rider_id:
        return redirect(url_for('auth.setup_profile'))

    rider = models.get_rider_by_id(rider_id)
    if not rider:
        abort(404)

    from shared.activity_feed import build_private_activity_feed

    try:
        strava_activities = [
            dict(row) for row in models.get_strava_activities(rider_id, days=120)
        ]
    except Exception:
        current_app.logger.exception(
            'Could not load Strava activities for rider %s', rider_id)
        strava_activities = []

    garmin_activities = []
    garmin_connection = None
    if current_app.config.get('GARMIN_TOKEN_ENCRYPTION_KEY'):
        try:
            garmin_connection = models.get_garmin_connection(rider_id)
            garmin_activities = models.get_garmin_brevet_match_review(
                rider_id, limit=50)
        except Exception:
            current_app.logger.exception(
                'Could not load Garmin activities for rider %s', rider_id)

    activity_feed = build_private_activity_feed(
        strava_activities=strava_activities,
        garmin_activities=garmin_activities,
    )
    from services.fitness import assess_readiness, score_all_activities

    # Apply the existing per-workout training rating after provider records have
    # been collapsed. A Garmin/Strava pair therefore receives one rating rather
    # than appearing as two independently scored workouts.
    ratings_by_strava_id = {
        str(row.get('strava_activity_id')): row
        for row in score_all_activities(strava_activities)
        if row.get('strava_activity_id') is not None
    }
    for activity in activity_feed:
        rating = ratings_by_strava_id.get(
            str(activity.get('strava_activity_id')))
        if rating:
            activity.update({
                'rating_score': rating.get('total'),
                'rating_grade': rating.get('grade'),
                'rating_color': rating.get('color'),
                'rating_trend': rating.get('trend'),
            })

    # Readiness belongs with the private upcoming-brevet calendar. Use only the
    # most recent 28 days even though the activity feed itself spans 120 days.
    recent_cutoff = datetime.now().date() - timedelta(days=28)
    recent_training = []
    for row in strava_activities:
        raw_date = row.get('start_date_local') or row.get('start_date')
        try:
            activity_date = datetime.fromisoformat(
                str(raw_date).replace('Z', '+00:00')).date()
        except (TypeError, ValueError):
            continue
        if activity_date >= recent_cutoff:
            recent_training.append(row)

    upcoming = []
    for row in models.get_rider_upcoming_signups(rider_id):
        ride = dict(row)
        ride['readiness'] = (
            assess_readiness(recent_training, ride)
            if strava_activities else None
        )
        upcoming.append(ride)
    strava_connection = models.get_strava_connection(rider_id)
    current_season = models.get_current_season()
    brevet_history = []
    try:
        for season in models.get_all_seasons():
            participation = [
                dict(row) for row in models.get_rider_participation(
                    rider_id, season['id'])
                if str(row.get('status') or '').lower() in ('finished', 'dnf')
            ]
            if participation:
                ride_ids = [
                    row['ride_id'] for row in participation if row.get('ride_id')]
                matches = models.get_all_strava_ride_matches(
                    rider_id, ride_ids) if strava_connection else {}
                brevet_history.append({
                    'season': dict(season),
                    'rides': participation,
                    'matches': matches,
                })
    except Exception:
        current_app.logger.exception(
            'Could not load brevet history for rider %s', rider_id)

    return render_template(
        'my_rides.html',
        rider=rider,
        activity_feed=activity_feed,
        upcoming=upcoming,
        current_season=current_season,
        brevet_history=brevet_history,
        strava_connection=strava_connection,
        garmin_connected=bool(garmin_connection),
    )


@auth_bp.route('/logout')
def logout():
    """Log out the current user."""
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('main.index'))
