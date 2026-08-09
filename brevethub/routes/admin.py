"""BrevetHub club-admin surface — owner-gated real ride-plan generation.

A club OWNER (rp_club.owner_rider_id == the signed-in rider) can paste an RWGPS URL
for a calendar brevet and generate a real, RWGPS-backed ride plan, persisted to
rp_brevet_route_plan[_stop] via the reused shared engine (build_ride_plan). The
guest /plan/<event_id> page then renders that persisted plan.

Ownership is the hard gate on every action here:
  - a signed-out visitor is bounced to login,
  - a signed-in rider who owns NO club gets 403,
  - only the rider who owns a club may generate plans (scoped to their club).

Generation FAILS SOFT: a missing RWGPS credential, an unparseable URL, or an RWGPS
API error flashes a message and redirects back — it never 500s (the reused
fetch_route raises on missing keys, so every call is guarded).

Isolation: imports only flask / stdlib / brevethub.* (its own models, decorators,
config) and brevethub.shared.* — nothing from Team Asha — so
test_brevethub_isolation.py stays green. Every model call is on an rp_* table.
"""
import hmac
import io
import json
import zlib
from datetime import datetime, time, timezone
import math
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, send_file, session, url_for)

from brevethub import models
from brevethub.decorators import current_rider, login_required
from brevethub.shared.operations_status import route_plan_status
from brevethub.shared.rwgps import (build_ride_plan, extract_controls,
                                    extract_rwgps_route_id, fetch_route)
from brevethub.shared.weather import fetch_historical_wind, headwind_component
from brevethub.services.ride_validation import (
    TrackPoint, combine_recordings, fingerprint, parse_fit, parse_gpx,
    validate_submission,
)
from brevethub.services.registration import progress_label

admin_bp = Blueprint('admin', __name__)


def _validation_visualization(submission):
    """Build compact official-route/activity data for the organizer comparison view."""
    route_id = extract_rwgps_route_id(submission.get('rwgps_url') or '')
    route = models.get_rp_route_elevation_track(route_id) if route_id else []
    raw_track = submission.get('normalized_track') or []
    if not route or len(raw_track) < 2:
        return {'route': route or [], 'track': raw_track, 'samples': [], 'wind_available': False}

    track = []
    distance_m = 0.0
    previous = None
    for row in raw_track:
        if len(row) < 3:
            continue
        try:
            timestamp = datetime.fromisoformat(str(row[2]).replace('Z', '+00:00'))
            point = {'lat': float(row[0]), 'lng': float(row[1]), 'timestamp': timestamp,
                     'elevation_m': float(row[3]) if len(row) > 3 and row[3] is not None else None}
        except (TypeError, ValueError):
            continue
        if previous:
            dlat = math.radians(point['lat'] - previous['lat'])
            dlng = math.radians(point['lng'] - previous['lng'])
            a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(previous['lat'])) * math.cos(math.radians(point['lat'])) * math.sin(dlng / 2) ** 2
            distance_m += 6371000 * 2 * math.asin(min(1, math.sqrt(a)))
            seconds = max(1, (point['timestamp'] - previous['timestamp']).total_seconds())
            point['speed_mph'] = distance_m and (distance_m - previous['distance_m']) / seconds * 2.236936 or 0
        else:
            point['speed_mph'] = 0
        point['distance_m'] = distance_m
        track.append(point)
        previous = point
    if len(track) < 2:
        return {'route': route, 'track': raw_track, 'samples': [], 'wind_available': False}

    route_samples = route[::max(1, len(route) // 180)]
    coords = [{'lat': float(p['lat']), 'lng': float(p['lng'])} for p in route_samples]
    winds = []
    try:
        winds, _ = fetch_historical_wind(coords[::max(1, len(coords) // 8)], submission['event_date'])
    except Exception:
        winds = []
    wind_coords = coords[::max(1, len(coords) // 8)] if winds else []
    samples = []
    for p in route_samples:
        route_dist = float(p.get('dist_m') or 0)
        activity = min(track, key=lambda t: abs(t['distance_m'] - route_dist))
        idx = route.index(p)
        before = route[max(0, idx - 1)]
        after = route[min(len(route) - 1, idx + 1)]
        run = max(1, float(after.get('dist_m') or 0) - float(before.get('dist_m') or 0))
        grade = ((float(after.get('e_m') or 0) - float(before.get('e_m') or 0)) / run) * 100
        headwind_mph = None
        if winds and wind_coords:
            wi = min(range(len(wind_coords)), key=lambda i: (wind_coords[i]['lat'] - p['lat']) ** 2 + (wind_coords[i]['lng'] - p['lng']) ** 2)
            weather = winds[wi].get('hourly', {}) if wi < len(winds) else {}
            times = weather.get('time') or []
            if times:
                target = activity['timestamp'].replace(tzinfo=None)
                ti = min(range(len(times)), key=lambda i: abs(datetime.fromisoformat(times[i]).replace(tzinfo=None) - target))
                speed_kmh = (weather.get('wind_speed_10m') or [0])[ti]
                direction = (weather.get('wind_direction_10m') or [0])[ti]
                bearing = math.degrees(math.atan2(after['lng'] - before['lng'], after['lat'] - before['lat'])) % 360
                headwind_mph = round(headwind_component(float(speed_kmh), float(direction), bearing) * 0.621371, 1)
        speed = round(float(activity.get('speed_mph') or 0), 1)
        anomaly = None
        if speed > 35 and grade > -5:
            anomaly = 'High speed for terrain'
        elif speed < 5 and grade < 3 and (headwind_mph is None or headwind_mph < 12):
            anomaly = 'Slow for grade/wind'
        samples.append({'distance_mi': round(route_dist / 1609.344, 1), 'lat': p['lat'], 'lng': p['lng'],
                        'elevation_ft': round(float(p.get('e_m') or 0) * 3.28084), 'speed_mph': speed,
                        'grade': round(grade, 2), 'headwind_mph': headwind_mph, 'anomaly': anomaly})
    route_json = [{'lat': float(p['lat']), 'lng': float(p['lng']), 'dist_m': float(p.get('dist_m') or 0),
                   'e_m': float(p.get('e_m') or 0)} for p in route]
    return {'route': route_json, 'track': [[p['lat'], p['lng']] for p in track], 'samples': samples,
            'wind_available': bool(winds)}


def operator_required(view):
    """Require a club-admin or super-admin session.

    Club admins have ``brevethub_operator_club_id`` set to their club's integer id.
    The super-admin (ADMIN_PASSWORD env var) has it set to the sentinel ``'__all__'``.
    Either value is truthy, so a single ``if not`` check gates both cases.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('brevethub_operator_club_id'):
            return redirect(url_for('admin.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _operator_club_id():
    """Return the club_id for the current operator, or None for super-admin."""
    val = session.get('brevethub_operator_club_id')
    if val == '__all__':
        return None
    return val


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    from werkzeug.security import check_password_hash
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        supplied = request.form.get('password') or ''

        # 1. Try club admin table first (username + password).
        if username:
            admin = models.get_club_admin_by_username(username)
            if admin and check_password_hash(admin['password_hash'], supplied):
                session['brevethub_operator_club_id'] = admin['club_id']
                session['brevethub_operator_username'] = admin['username']
                session['brevethub_operator_club_name'] = admin['club_name']
                session['brevethub_operator_region_prefix'] = admin.get('region_prefix')
                models.record_club_admin_login(admin['id'])
                return redirect(request.args.get('next') or url_for('admin.events'))

        # 2. Fall back to global super-admin password (no username required).
        configured = current_app.config.get('ADMIN_PASSWORD')
        if configured and hmac.compare_digest(supplied, configured):
            session['brevethub_operator_club_id'] = '__all__'
            session['brevethub_operator_username'] = 'superadmin'
            session['brevethub_operator_club_name'] = 'All clubs'
            return redirect(request.args.get('next') or url_for('admin.dashboard'))

        flash('Incorrect username or password.', 'error')
    return render_template('admin_login.html')


@admin_bp.route('/logout', methods=['POST'])
def logout():
    session.pop('brevethub_operator_club_id', None)
    session.pop('brevethub_operator_username', None)
    session.pop('brevethub_operator_club_name', None)
    session.pop('brevethub_operator_region_prefix', None)
    # Legacy key — remove if present from old sessions.
    session.pop('brevethub_operator', None)
    return redirect(url_for('main.landing'))


@admin_bp.route('/', methods=['GET'])
@operator_required
def dashboard():
    try:
        pipeline_status = route_plan_status(
            models.get_route_plan_operations_status())
        finishers_pending = len(models.get_signups_needing_finish_time())
    except Exception:
        current_app.logger.exception('Could not load BrevetHub operations status')
        pipeline_status = route_plan_status({})
        finishers_pending = 0
    return render_template(
        'admin_dashboard.html',
        pipeline_status=pipeline_status,
        finishers_pending=finishers_pending,
    )


_MAX_VALIDATION_UPLOAD = 4 * 1024 * 1024
_ORGANIZER_DECISIONS = {'approved', 'needs_more_evidence', 'not_approved'}
_STATE_ZONES = {
    **{s: 'America/Los_Angeles' for s in ('CA', 'NV', 'OR', 'WA')},
    **{s: 'America/Denver' for s in ('CO', 'ID', 'MT', 'NM', 'UT', 'WY')},
    'AZ': 'America/Phoenix',
    **{s: 'America/Chicago' for s in ('AL', 'AR', 'IA', 'IL', 'KS', 'LA', 'MN',
                                      'MO', 'MS', 'ND', 'NE', 'OK', 'SD', 'TN',
                                      'TX', 'WI')},
    **{s: 'America/New_York' for s in ('CT', 'DC', 'DE', 'FL', 'GA', 'IN', 'KY',
                                       'MA', 'MD', 'ME', 'MI', 'NC', 'NH', 'NJ',
                                       'NY', 'OH', 'PA', 'RI', 'SC', 'VA', 'VT',
                                       'WV')},
    'AK': 'America/Anchorage', 'HI': 'Pacific/Honolulu',
}


def _official_start(event, override=None):
    """Return an aware official event start; an explicit ISO value wins."""
    if override:
        value = datetime.fromisoformat(override.replace('Z', '+00:00'))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if not event.get('start_time'):
        return None
    start = str(event['start_time']).split(':')
    hour, minute = int(start[0]), int(start[1]) if len(start) > 1 else 0
    region = str(event.get('region') or '')
    state = region.split(':', 1)[0].strip().upper()
    zone = ZoneInfo(_STATE_ZONES.get(state, 'UTC'))
    return datetime.combine(event['date'], time(hour, minute), tzinfo=zone)


def _track_json(points):
    cap = max(1, len(points) // 5000)
    return [[round(p.lat, 6), round(p.lng, 6), p.timestamp.isoformat(),
             round(float(p.elevation_m), 1) if p.elevation_m is not None else None]
            for p in points[::cap]]


def _points_from_cached_strava(row, started_at):
    if not row or not row.get('activity_streams'):
        raise ValueError('That Strava activity has no cached streams. Analyze it first.')
    streams = json.loads(zlib.decompress(bytes(row['activity_streams'])))
    latlng, seconds = streams.get('latlng') or [], streams.get('time') or []
    elevation = streams.get('altitude') or []
    if len(latlng) < 2 or len(seconds) != len(latlng):
        raise ValueError('The cached Strava activity has no complete GPS/time stream.')
    start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    from datetime import timedelta
    points = []
    for idx, pair in enumerate(latlng):
        if not pair or len(pair) < 2:
            continue
        points.append(TrackPoint(start + timedelta(seconds=float(seconds[idx])),
                                 float(pair[0]), float(pair[1]),
                                 elevation[idx] if idx < len(elevation) else None))
    return combine_recordings([points])


@admin_bp.route('/validations', methods=['GET'])
@operator_required
def validations():
    return render_template('admin/validations.html',
                           submissions=models.list_validation_submissions())


@admin_bp.route('/validations/new', methods=['GET', 'POST'])
@operator_required
def validation_new():
    candidates = models.get_validation_candidates()
    if request.method == 'GET':
        return render_template('admin/validation_new.html', candidates=candidates)

    try:
        event_id = int(request.form.get('event_id') or 0)
        rider_id = int(request.form.get('rider_id') or 0)
    except ValueError:
        event_id = rider_id = 0
    event = models.get_brevet_event_full(event_id)
    if not event or not any(int(c['event_id']) == event_id and int(c['rider_id']) == rider_id for c in candidates):
        abort(400)

    recordings, file_rows, metadata = [], [], {}
    total_bytes = 0
    for uploaded in request.files.getlist('recordings'):
        if not uploaded or not uploaded.filename:
            continue
        data = uploaded.read()
        total_bytes += len(data)
        if total_bytes > _MAX_VALIDATION_UPLOAD:
            flash('Recording uploads must total 4 MB or less.', 'error')
            return render_template('admin/validation_new.html', candidates=candidates), 413
        suffix = uploaded.filename.rsplit('.', 1)[-1].lower()
        try:
            points, parsed_metadata = parse_fit(data) if suffix == 'fit' else parse_gpx(data)
        except Exception as exc:
            flash(f'Could not parse {uploaded.filename}: {exc}', 'error')
            return render_template('admin/validation_new.html', candidates=candidates), 400
        recordings.append(points)
        metadata.update({k: v for k, v in parsed_metadata.items() if v is not None})
        file_rows.append(('recording', uploaded, data, fingerprint(data), None))

    strava_id = request.form.get('strava_activity_id', '').strip()
    if strava_id:
        if not request.form.get('activity_started_at'):
            flash('Enter the Strava activity start time with its timezone.', 'error')
            return render_template('admin/validation_new.html', candidates=candidates), 400
        cached = models.get_ride_analysis(rider_id, int(strava_id))
        try:
            recordings.append(_points_from_cached_strava(cached, request.form['activity_started_at']))
        except Exception as exc:
            flash(str(exc), 'error')
            return render_template('admin/validation_new.html', candidates=candidates), 400
        metadata.update({'format': 'strava_stream', 'source': 'Strava'})

    metadata.update({
        'device': (request.form.get('source_device') or metadata.get('device') or '').strip() or None,
        'activity_type': (request.form.get('activity_type') or metadata.get('activity_type') or '').strip() or None,
        'manual': request.form.get('manual_activity') == '1',
        'recording_count': len(recordings),
    })

    evidence_orders = set()
    for raw_order in (request.form.get('control_evidence_orders') or '').split(','):
        try:
            if raw_order.strip():
                evidence_orders.add(int(raw_order.strip()))
        except ValueError:
            flash('Control proof orders must be comma-separated whole numbers.', 'error')
            return render_template('admin/validation_new.html', candidates=candidates), 400
    traditional = False
    for uploaded in request.files.getlist('proof_files'):
        if not uploaded or not uploaded.filename:
            continue
        data = uploaded.read()
        total_bytes += len(data)
        if total_bytes > _MAX_VALIDATION_UPLOAD:
            flash('All evidence uploads must total 4 MB or less.', 'error')
            return render_template('admin/validation_new.html', candidates=candidates), 413
        traditional = True
        file_rows.append(('traditional', uploaded, data, fingerprint(data), None))
    proof_description = (request.form.get('proof_description') or '').strip()
    traditional = traditional or bool(proof_description)

    points = combine_recordings(recordings) if recordings else []
    route_plan = models.get_brevet_route_plan_with_stops(event_id) or {'plan': {}, 'stops': []}
    route_id = (route_plan.get('plan') or {}).get('rwgps_route_id')
    route = models.get_rp_route_elevation_track(route_id) if route_id else []
    hashes = [row[3] for row in file_rows]
    conflicts = models.find_validation_evidence_conflicts(
        hashes, event_id=event_id, rider_id=rider_id,
        strava_activity_id=int(strava_id) if strava_id else None)
    decision, checks = validate_submission(
        points=points, route=route or [], controls=route_plan.get('stops') or [],
        event=dict(event), official_start=_official_start(event, request.form.get('official_start')),
        evidence_control_orders=evidence_orders, source_metadata=metadata,
        duplicate_conflicts=[dict(c) for c in conflicts], has_traditional_evidence=traditional,
    )
    source_type = 'mixed' if recordings and traditional else ('traditional' if traditional and not recordings else ('strava' if strava_id and not file_rows else 'file'))
    created = models.create_validation_submission(
        event_id=event_id, rider_id=rider_id, source_type=source_type,
        strava_activity_id=int(strava_id) if strava_id else None,
        source_metadata=metadata, normalized_track=_track_json(points),
        rider_explanation=request.form.get('rider_explanation'),
    )
    submission_id = created['id']
    for kind, uploaded, data, digest, control_order in file_rows:
        models.add_validation_evidence(
            submission_id, evidence_kind=kind, filename=uploaded.filename,
            content_type=uploaded.content_type, content=data, sha256=digest,
            control_order=control_order,
            control_orders=sorted(evidence_orders) if kind == 'traditional' else [],
            description=proof_description if kind == 'traditional' else None,
        )
    if proof_description and not any(row[0] == 'traditional' for row in file_rows):
        models.add_validation_evidence(submission_id, evidence_kind='traditional',
                                       control_orders=sorted(evidence_orders),
                                       description=proof_description)
    models.replace_validation_checks(submission_id, decision, checks)
    flash('Evidence analyzed. An organizer still makes the final decision.', 'success')
    return redirect(url_for('admin.validation_detail', submission_id=submission_id))


@admin_bp.route('/validations/<int:submission_id>', methods=['GET'])
@operator_required
def validation_detail(submission_id):
    submission = models.get_validation_submission(submission_id)
    if not submission:
        abort(404)
    return render_template('admin/validation_detail.html', submission=submission,
                           checks=models.get_validation_checks(submission_id),
                           evidence=models.get_validation_evidence(submission_id),
                           visualization=_validation_visualization(submission))


@admin_bp.route('/validations/<int:submission_id>/evidence/<int:evidence_id>', methods=['GET'])
@operator_required
def validation_evidence(submission_id, evidence_id):
    evidence = models.get_validation_evidence_content(submission_id, evidence_id)
    if not evidence or evidence.get('private_content') is None:
        abort(404)
    content_type = evidence.get('content_type') or 'application/octet-stream'
    inline = content_type.startswith('image/') or content_type == 'application/pdf'
    return send_file(
        io.BytesIO(bytes(evidence['private_content'])), mimetype=content_type,
        download_name=evidence.get('original_filename') or f'evidence-{evidence_id}',
        as_attachment=not inline, max_age=0,
    )


@admin_bp.route('/events', methods=['GET'])
@operator_required
def events():
    """Club-scoped events view: this week / upcoming / past, with rider counts.

    Club admins see only their club's events. Super-admins see all clubs.
    """
    from datetime import date, timedelta
    include_past = request.args.get('past') == '1'
    club_id = _operator_club_id()
    region_prefix = session.get('brevethub_operator_region_prefix')
    all_events = models.get_club_admin_events(club_id, include_past=include_past,
                                              region_prefix=region_prefix)
    today = date.today()
    week_end = today + timedelta(days=7)

    this_week, upcoming, past = [], [], []
    for ev in all_events:
        ev_date = ev['date'] if hasattr(ev['date'], 'year') else date.fromisoformat(str(ev['date']))
        if ev_date < today:
            past.append(ev)
        elif ev_date <= week_end:
            this_week.append(ev)
        else:
            upcoming.append(ev)

    # Past comes back ASC from the query; reverse so newest past is first
    past = list(reversed(past))

    return render_template(
        'admin/events.html',
        this_week=this_week,
        upcoming=upcoming,
        past=past,
        include_past=include_past,
        admin_club_id=club_id,
    )


@admin_bp.route('/registrations', methods=['GET'])
@operator_required
def registrations():
    return render_template(
        'admin/registrations.html',
        events=models.list_registration_events(),
        exceptions=models.list_registration_exceptions(limit=25),
    )


def _assert_event_club_access(event):
    """Abort 403 if the operator is not super-admin and doesn't own this event.

    Events from the RUSA national feed have club_id = NULL; ownership is
    determined by region_prefix match in that case.
    """
    if _is_super_admin():
        return
    club_id = _operator_club_id()
    region_prefix = session.get('brevethub_operator_region_prefix')
    event_club_id = event.get('club_id')
    event_region = event.get('region') or ''
    # Match by explicit club_id assignment
    if event_club_id is not None and event_club_id == club_id:
        return
    # Match by region prefix for RUSA feed events (club_id = NULL)
    if region_prefix and event_region == region_prefix:
        return
    abort(403)


@admin_bp.route('/registrations/event/<int:event_id>', methods=['GET', 'POST'])
@operator_required
def event_roster(event_id):
    event = models.get_brevet_event_registration(event_id)
    if not event:
        abort(404)
    _assert_event_club_access(event)

    if request.method == 'POST':
        action = request.form.get('action')
        rider_id = request.form.get('rider_id', type=int)
        if action == 'remove' and rider_id:
            models.admin_remove_event_signup(event_id, rider_id)
            flash('Rider removed from event roster.', 'success')
        elif rider_id:
            status = (request.form.get('status') or '').strip().lower()
            finish_time = (request.form.get('finish_time') or '').strip() or None
            reg_status = (request.form.get('registration_status') or '').strip() or None
            try:
                models.admin_update_event_signup(
                    event_id, rider_id, status,
                    finish_time=finish_time,
                    registration_status=reg_status,
                )
                flash('Rider status updated.', 'success')
            except ValueError as exc:
                flash(str(exc), 'error')
        else:
            for key, value in request.form.items():
                if not key.startswith('status_') or not value:
                    continue
                try:
                    rid = int(key.split('_', 1)[1])
                    models.admin_update_event_signup(event_id, rid, value.strip())
                except (ValueError, IndexError):
                    continue
            flash('Roster statuses saved.', 'success')
        return redirect(url_for('admin.event_roster', event_id=event_id))

    roster = models.get_admin_event_roster(event_id)
    for row in roster:
        row['progress'] = progress_label(
            event_past=bool(row.get('event_past')),
            status=row.get('status') or '',
            registration_status=row.get('registration_status'),
        )
    return render_template(
        'admin/event_roster.html',
        event=event,
        roster=roster,
    )


@admin_bp.route('/registrations/event/<int:event_id>/export.csv')
@operator_required
def export_roster_csv(event_id):
    """Download the event roster as CSV."""
    import csv
    event = models.get_brevet_event_registration(event_id)
    if not event:
        abort(404)
    _assert_event_club_access(event)
    roster = models.get_admin_event_roster(event_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'First Name', 'Last Name', 'Email', 'Phone', 'RUSA ID',
        'Reg. Status', 'Ride Status', 'Finish Time',
        'Exception Reason', 'Confirmation Code',
    ])
    for r in roster:
        writer.writerow([
            r.get('first_name') or '',
            r.get('last_name') or '',
            r.get('email') or '',
            r.get('phone') or '',
            r.get('rusa_id') or '',
            r.get('registration_status') or '',
            r.get('status') or '',
            r.get('finish_time') or '',
            r.get('exception_reason') or '',
            r.get('confirmation_code') or '',
        ])
    fname = f"roster_{event_id}_{event.get('date', '')}.csv".replace(' ', '_')
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )


@admin_bp.route('/validations/<int:submission_id>/decision', methods=['POST'])
@operator_required
def validation_decision(submission_id):
    decision = request.form.get('organizer_decision')
    if decision not in _ORGANIZER_DECISIONS or not models.get_validation_submission(submission_id):
        abort(400)
    models.set_validation_organizer_decision(submission_id, decision,
                                             (request.form.get('organizer_notes') or '').strip())
    flash('Organizer decision saved.', 'success')
    return redirect(url_for('admin.validation_detail', submission_id=submission_id))


@admin_bp.route('/run/<operation>', methods=['POST'])
@operator_required
def run_operation(operation):
    # Import lazily so the cron module remains the single owner of each pipeline
    # implementation and admin.py does not create an import cycle at app startup.
    from brevethub.routes.cron import (
        run_backfill_rwgps_urls,
        run_fetch_brevet_weather,
        run_refresh_calendar,
        run_refresh_eddington,
        run_sync_rusa_results,
        run_sync_sfr_registration,
        run_warm_brevet_plans,
        run_warm_brevet_route_weather,
        run_warm_plan_elevation,
    )
    operations = {
        'refresh-calendar': run_refresh_calendar,
        'sync-sfr-registration': run_sync_sfr_registration,
        'sync-rusa-results': run_sync_rusa_results,
        'backfill-rwgps': run_backfill_rwgps_urls,
        'warm-plans': run_warm_brevet_plans,
        'fetch-weather': run_fetch_brevet_weather,
        'warm-route-weather': run_warm_brevet_route_weather,
        'warm-elevation': run_warm_plan_elevation,
        'refresh-eddington': run_refresh_eddington,
    }
    runner = operations.get(operation)
    if runner is None:
        abort(404)
    result = runner()
    category = 'success' if result.get('ok') else 'error'
    details = ', '.join(f'{key.replace("_", " ")}: {value}'
                        for key, value in result.items() if key != 'ok')
    flash(f'{operation.replace("-", " ").title()}: {details}', category)
    return redirect(url_for('admin.dashboard'))


def _is_super_admin():
    return session.get('brevethub_operator_club_id') == '__all__'


def _rbac_assert_club_access(target_club_id):
    """Abort 403 if the operator is not super-admin and target_club_id != their club."""
    if _is_super_admin():
        return
    if target_club_id != _operator_club_id():
        abort(403)


def _rbac_assert_admin_access(admin_row):
    """Abort 403 if the operator doesn't own the club of the target admin row."""
    if not admin_row:
        abort(404)
    _rbac_assert_club_access(admin_row['club_id'])


@admin_bp.route('/rbac', methods=['GET'])
@operator_required
def rbac():
    """RBAC management — list all club admins scoped to the operator's club."""
    if _is_super_admin():
        admins = models.list_all_club_admins()
        clubs = models.list_all_clubs_for_admin()
        scoped_club_id = None
    else:
        club_id = _operator_club_id()
        admins = models.list_club_admins(club_id)
        clubs = []
        scoped_club_id = club_id
    return render_template(
        'admin/rbac.html',
        admins=admins,
        clubs=clubs,
        scoped_club_id=scoped_club_id,
        is_super_admin=_is_super_admin(),
    )


@admin_bp.route('/rbac/create', methods=['POST'])
@operator_required
def rbac_create():
    """Create a new club admin account."""
    from werkzeug.security import generate_password_hash
    import psycopg2

    if _is_super_admin():
        try:
            club_id = int(request.form.get('club_id') or 0)
        except (ValueError, TypeError):
            flash('Select a valid club.', 'error')
            return redirect(url_for('admin.rbac'))
    else:
        club_id = _operator_club_id()

    _rbac_assert_club_access(club_id)

    username = (request.form.get('username') or '').strip().lower()
    password = request.form.get('password') or ''
    display_name = (request.form.get('display_name') or '').strip() or None

    if not username or len(username) < 3:
        flash('Username must be at least 3 characters.', 'error')
        return redirect(url_for('admin.rbac'))
    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('admin.rbac'))

    try:
        models.create_club_admin(club_id, username, generate_password_hash(password),
                                 display_name=display_name)
        flash(f'Admin account "{username}" created.', 'success')
    except psycopg2.errors.UniqueViolation:
        flash(f'Username "{username}" is already taken.', 'error')
    except Exception as exc:
        current_app.logger.exception('Failed to create club admin')
        flash(f'Could not create admin: {exc}', 'error')
    return redirect(url_for('admin.rbac'))


@admin_bp.route('/rbac/<int:admin_id>/deactivate', methods=['POST'])
@operator_required
def rbac_deactivate(admin_id):
    """Deactivate (soft-delete) a club admin account."""
    admin = models.get_club_admin_by_id(admin_id)
    _rbac_assert_admin_access(admin)
    models.deactivate_club_admin(admin_id)
    flash(f'Admin "{admin["username"]}" deactivated.', 'success')
    return redirect(url_for('admin.rbac'))


@admin_bp.route('/rbac/<int:admin_id>/reactivate', methods=['POST'])
@operator_required
def rbac_reactivate(admin_id):
    """Re-enable a deactivated club admin account."""
    admin = models.get_club_admin_by_id(admin_id)
    _rbac_assert_admin_access(admin)
    models.reactivate_club_admin(admin_id)
    flash(f'Admin "{admin["username"]}" reactivated.', 'success')
    return redirect(url_for('admin.rbac'))


@admin_bp.route('/rbac/<int:admin_id>/reset-password', methods=['POST'])
@operator_required
def rbac_reset_password(admin_id):
    """Reset the password for a club admin account."""
    from werkzeug.security import generate_password_hash
    admin = models.get_club_admin_by_id(admin_id)
    _rbac_assert_admin_access(admin)
    password = request.form.get('password') or ''
    if len(password) < 8:
        flash('New password must be at least 8 characters.', 'error')
        return redirect(url_for('admin.rbac'))
    models.update_club_admin_password(admin_id, generate_password_hash(password))
    flash(f'Password reset for "{admin["username"]}".', 'success')
    return redirect(url_for('admin.rbac'))


def _owned_club_or_403():
    """The club the signed-in rider owns, or abort 403 when they own none.

    The single ownership gate for this blueprint. A signed-out rider never reaches
    here (login_required runs first); a signed-in rider who owns no club is 403.
    """
    rider = current_rider()
    owned = models.get_club_owned_by_rider(rider['id']) if rider else None
    if not owned:
        abort(403)
    return rider, owned


@admin_bp.route('/plan', methods=['GET'])
@login_required
def plan_console():
    """Owner console: pick an upcoming brevet + paste an RWGPS URL to generate a plan.

    Owner → 200 with the generate form and a reference list of upcoming events;
    signed-in non-owner → 403.
    """
    _rider, owned = _owned_club_or_403()
    events = models.get_upcoming_events(limit=100)
    try:
        pipeline_status = route_plan_status(
            models.get_route_plan_operations_status())
    except Exception:
        current_app.logger.exception('Could not load route-plan pipeline status')
        pipeline_status = route_plan_status({})
    return render_template(
        'admin_plan.html',
        owned_club=owned,
        events=events,
        pipeline_status=pipeline_status,
    )


@admin_bp.route('/plan/generate', methods=['POST'])
@login_required
def generate_plan():
    """Generate + persist a real RWGPS plan for a brevet (owner only).

    Reads event_id + an RWGPS URL (falling back to the event's cached rwgps_url),
    builds the plan via the reused shared engine using the BrevetHub RWGPS
    credentials, and upserts it scoped to the owner's club. Fails soft on any
    RWGPS/build error (flash + redirect, never 500).
    """
    _rider, owned = _owned_club_or_403()

    event_id_raw = (request.form.get('event_id') or '').strip()
    try:
        event_id = int(event_id_raw)
    except (TypeError, ValueError):
        flash('Pick a valid brevet to generate a plan for.', 'error')
        return redirect(url_for('admin.plan_console'))

    event = models.get_brevet_event_full(event_id)
    if not event:
        flash('That brevet is not in the calendar.', 'error')
        return redirect(url_for('admin.plan_console'))

    # Authority gate: an owner may generate a plan for an event that belongs to
    # their own club, or for a national-feed event with no club (club_id NULL,
    # first-owner-wins claimable). Generating for ANOTHER club's known event is
    # forbidden — otherwise a first-owner-wins claim would lock the rightful club
    # out of its own brevet's public plan.
    if event.get('club_id') is not None and event['club_id'] != owned['id']:
        abort(403)

    rwgps_url = (request.form.get('rwgps_url') or '').strip() or event.get('rwgps_url')
    route_id = extract_rwgps_route_id(rwgps_url)
    if not route_id:
        flash('Enter a valid RideWithGPS route URL (e.g. ridewithgps.com/routes/123).',
              'error')
        return redirect(url_for('admin.plan_console'))

    api_key = current_app.config.get('RWGPS_API_KEY')
    auth_token = current_app.config.get('RWGPS_AUTH_TOKEN')
    start_time = event.get('start_time')
    try:
        route_data = fetch_route(route_id, api_key, auth_token)
        controls = extract_controls(route_data)
        # Build + persist BOTH variants: conservative (the graded/display default) and
        # aggressive (+1.5 mph, display-only), each with clock-typed meal breaks. Fetch
        # the route once; the two builds differ only in their pacing profile.
        plan_id = None
        for variant in ('conservative', 'aggressive'):
            built = build_ride_plan(route_data, controls, profile=variant,
                                    insert_meals=True, start_time=start_time)
            written = models.upsert_brevet_route_plan(
                event_id, built['plan'], built['stops'],
                club_id=owned['id'], variant=variant)
            if written is None:
                # Another club owns this brevet's plan — stop before the second variant
                # so we never half-write; the guard blocks both identically.
                plan_id = None
                break
            if variant == 'conservative':
                plan_id = written
    except Exception as e:
        current_app.logger.warning(
            'Admin plan generation failed for event %s (route %s): %s',
            event_id, route_id, e)
        flash(f'Could not generate the plan: {e}', 'error')
        return redirect(url_for('admin.plan_console'))

    if plan_id is None:
        # Another club already owns this brevet's public plan (first-owner-wins).
        flash("This brevet's ride plan is already managed by another club.", 'error')
        return redirect(url_for('admin.plan_console'))

    flash(f'Real ride plan generated for {event["name"]}.', 'success')
    return redirect(url_for('plan.plan_view', event_id=event_id))
