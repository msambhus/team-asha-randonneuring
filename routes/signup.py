"""Rider signup routes for upcoming rides."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, session
from models import (get_ride_by_id, get_signups_for_ride, get_all_riders, signup_rider,
                    mark_interested, mark_maybe, mark_withdraw, remove_signup, 
                    get_rider_signup_status, get_user_by_id, RideStatus)
from auth import login_required
from cache import cache

signup_bp = Blueprint('signup', __name__)


@signup_bp.route('/<int:ride_id>', methods=['GET', 'POST'])
@login_required
def ride_signup(ride_id):
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    if request.method == 'POST':
        rider_id = request.form.get('rider_id', type=int)
        action = request.form.get('action', 'signup')
        if rider_id:
            if action == 'remove':
                remove_signup(rider_id, ride_id)
            else:
                signup_rider(rider_id, ride_id)
            cache.clear()  # Clear cache after signup change
        return redirect(url_for('signup.ride_signup', ride_id=ride_id))

    signups = get_signups_for_ride(ride_id)
    signup_ids = {r['id'] for r in signups}
    all_riders = get_all_riders()

    return render_template('signup.html',
                           ride=ride,
                           signups=signups,
                           signup_ids=signup_ids,
                           all_riders=all_riders)


@signup_bp.route('/api/<int:ride_id>/signup', methods=['POST'])
def api_signup(ride_id):
    """API endpoint to sign up current user for a ride. Allows status changes."""
    user_id = session.get('user_id')
    if not user_id:
        # Store current page for redirect after login
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        login_url = url_for('auth.login', next=referer, _external=False)
        return jsonify({'success': False, 'error': 'Not logged in', 'redirect': login_url}), 401
    
    # Get user's rider_id
    user = get_user_by_id(user_id)
    if not user or not user.get('rider_id'):
        # Store current page for redirect after profile setup
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        session['next_url'] = referer
        return jsonify({'success': False, 'error': 'Profile not completed', 'redirect': url_for('auth.setup_profile', _external=False)}), 400
    
    rider_id = user['rider_id']
    
    # Sign up the rider (allows status transitions)
    success = signup_rider(rider_id, ride_id)
    if success:
        cache.clear()  # Clear cache after signup
        return jsonify({'success': True, 'status': 'GOING'})
    else:
        return jsonify({'success': False, 'error': 'Failed to sign up'}), 500


@signup_bp.route('/api/<int:ride_id>/interested', methods=['POST'])
def api_interested(ride_id):
    """API endpoint to mark current user as interested in a ride. Allows status changes."""
    user_id = session.get('user_id')
    if not user_id:
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        login_url = url_for('auth.login', next=referer, _external=False)
        return jsonify({'success': False, 'error': 'Not logged in', 'redirect': login_url}), 401

    user = get_user_by_id(user_id)
    if not user or not user.get('rider_id'):
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        session['next_url'] = referer
        return jsonify({'success': False, 'error': 'Profile not completed', 'redirect': url_for('auth.setup_profile', _external=False)}), 400

    rider_id = user['rider_id']

    # Mark as interested (allows status transitions)
    success = mark_interested(rider_id, ride_id)
    if success:
        cache.clear()  # Clear cache after marking interest
        return jsonify({'success': True, 'status': 'INTERESTED'})
    return jsonify({'success': False, 'error': 'Failed to mark interest'}), 500


@signup_bp.route('/api/<int:ride_id>/maybe', methods=['POST'])
def api_maybe(ride_id):
    """API endpoint to mark current user as maybe for a ride. Allows status changes."""
    user_id = session.get('user_id')
    if not user_id:
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        login_url = url_for('auth.login', next=referer, _external=False)
        return jsonify({'success': False, 'error': 'Not logged in', 'redirect': login_url}), 401

    user = get_user_by_id(user_id)
    if not user or not user.get('rider_id'):
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        session['next_url'] = referer
        return jsonify({'success': False, 'error': 'Profile not completed', 'redirect': url_for('auth.setup_profile', _external=False)}), 400

    rider_id = user['rider_id']

    success = mark_maybe(rider_id, ride_id)
    if success:
        cache.clear()  # Clear cache after marking maybe
        return jsonify({'success': True, 'status': 'MAYBE'})
    return jsonify({'success': False, 'error': 'Failed to mark as maybe'}), 500


@signup_bp.route('/api/<int:ride_id>/withdraw', methods=['POST'])
def api_withdraw(ride_id):
    """API endpoint to mark current user as withdrawn from a ride."""
    user_id = session.get('user_id')
    if not user_id:
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        login_url = url_for('auth.login', next=referer, _external=False)
        return jsonify({'success': False, 'error': 'Not logged in', 'redirect': login_url}), 401

    user = get_user_by_id(user_id)
    if not user or not user.get('rider_id'):
        referer = request.headers.get('Referer', url_for('riders.upcoming_brevets', _external=False))
        session['next_url'] = referer
        return jsonify({'success': False, 'error': 'Profile not completed', 'redirect': url_for('auth.setup_profile', _external=False)}), 400

    rider_id = user['rider_id']

    success = mark_withdraw(rider_id, ride_id)
    if success:
        cache.clear()  # Clear cache after withdrawing
        return jsonify({'success': True, 'status': 'WITHDRAW'})
    return jsonify({'success': False, 'error': 'Failed to mark as withdrawn'}), 500


@signup_bp.route('/api/<int:ride_id>/unsignup', methods=['POST'])
def api_unsignup(ride_id):
    """API endpoint to remove current user's signup for a ride."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in', 'redirect': '/auth/login'}), 401
    
    # Get user's rider_id
    user = get_user_by_id(user_id)
    if not user or not user.get('rider_id'):
        return jsonify({'success': False, 'error': 'Profile not completed', 'redirect': '/auth/setup-profile'}), 400
    
    rider_id = user['rider_id']
    
    # Remove signup (works for pre-ride statuses: GOING, INTERESTED, MAYBE)
    success = remove_signup(rider_id, ride_id)
    if success:
        cache.clear()  # Clear cache after removing signup
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Cannot remove signup (may have already started/finished)'}), 400


@signup_bp.route('/api/<int:ride_id>/signups', methods=['GET'])
def api_get_signups(ride_id):
    """API endpoint to get all signups for a ride (public)."""
    signups = get_signups_for_ride(ride_id)
    return jsonify({
        'success': True,
        'count': len(signups),
        'riders': [{
            'id': s['id'],
            'rusa_id': s['rusa_id'],
            'first_name': s['first_name'],
            'last_name': s['last_name'],
            'status': s['status'],
            'signed_up_at': s['signed_up_at'].isoformat() if s.get('signed_up_at') else None
        } for s in signups]
    })
