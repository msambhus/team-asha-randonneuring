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
    # Build a control-by-control comparison from the persisted plan and the
    # submitted track. Stream metrics are optional: FIT/Strava recordings may
    # not contain heart rate or power.
    segment_rows = []
    plan_bundle = models.get_brevet_route_plan_with_stops(submission['event_id']) or {'stops': []}
    stream_metrics = {}
    if submission.get('strava_activity_id'):
        cached = models.get_ride_analysis(submission['rider_id'], int(submission['strava_activity_id']))
        blob = cached.get('activity_streams') if cached else None
        if blob:
            try:
                stream_metrics = json.loads(zlib.decompress(bytes(blob)))
            except (TypeError, ValueError, zlib.error, json.JSONDecodeError):
                stream_metrics = {}
    previous_mi = 0.0
    event = models.get_brevet_event_full(submission['event_id']) or submission
    official_start = _official_start(event)

    def fmt_minutes(value):
        if value is None:
            return '—'
        value = int(round(float(value)))
        return f'{value // 60}:{value % 60:02d}'

    distance_stream = stream_metrics.get('distance') or []
    hr_stream = stream_metrics.get('heartrate') or []
    power_stream = stream_metrics.get('watts') or []
    time_stream = stream_metrics.get('time') or []
    for stop in plan_bundle.get('stops') or []:
        if str(stop.get('stop_type') or '').lower() in ('start', 'finish'):
            continue
        end_mi = float(stop.get('distance_miles') or 0)
        start_mi = previous_mi
        previous_mi = end_mi
        segment_points = [p for p in track if start_mi * 1609.344 <= p['distance_m'] <= end_mi * 1609.344]
        if len(segment_points) >= 2:
            elapsed_s = max(0, (segment_points[-1]['timestamp'] - segment_points[0]['timestamp']).total_seconds())
            moving_s = sum(max(0, (b['timestamp'] - a['timestamp']).total_seconds())
                           for a, b in zip(segment_points, segment_points[1:]) if b.get('speed_mph', 0) >= 1)
            moving_distance_m = max(0, segment_points[-1]['distance_m'] - segment_points[0]['distance_m'])
            avg_speed = moving_distance_m / max(1, moving_s) * 2.236936
            actual_elapsed_min = ((segment_points[-1]['timestamp'] - official_start).total_seconds() / 60
                                  if official_start else None)
        else:
            elapsed_s = moving_s = 0
            avg_speed = actual_elapsed_min = None
        stream_values = [i for i, d in enumerate(distance_stream)
                         if d is not None and start_mi * 1609.344 <= float(d) <= end_mi * 1609.344]
        avg_hr = (sum(float(hr_stream[i]) for i in stream_values if i < len(hr_stream) and hr_stream[i] is not None) /
                  max(1, sum(1 for i in stream_values if i < len(hr_stream) and hr_stream[i] is not None))) if stream_values else None
        avg_power = (sum(float(power_stream[i]) for i in stream_values if i < len(power_stream) and power_stream[i] is not None) /
                     max(1, sum(1 for i in stream_values if i < len(power_stream) and power_stream[i] is not None))) if stream_values else None
        wind_values = [s['headwind_mph'] for s in samples if start_mi <= s['distance_mi'] <= end_mi and s.get('headwind_mph') is not None]
        segment_rows.append({
            'order': stop.get('stop_order'), 'control': stop.get('location') or stop.get('notes') or 'Control',
            'distance_mi': round(end_mi, 1), 'segment_mi': round(max(0, end_mi - start_mi), 1),
            'cutoff': fmt_minutes(stop.get('bookend_time_min')), 'plan_bank': fmt_minutes(stop.get('time_bank_min')),
            'ft_per_mile': round(float(stop.get('ft_per_mi') or 0)),
            'headwind_mph': round(sum(wind_values) / len(wind_values), 1) if wind_values else None,
            'speed_mph': round(avg_speed, 1) if avg_speed is not None else None,
            'elapsed': fmt_minutes(elapsed_s / 60) if elapsed_s else '—',
            'moving': fmt_minutes(moving_s / 60) if moving_s else '—',
            'heart_rate': round(avg_hr) if avg_hr is not None else None,
            'power': round(avg_power) if avg_power is not None else None,
            'actual_bank': fmt_minutes(float(stop.get('bookend_time_min')) - actual_elapsed_min) if actual_elapsed_min is not None and stop.get('bookend_time_min') is not None else '—',
        })
    chart_w, chart_h = 980, 280
    max_distance = max((s['distance_mi'] for s in samples), default=1) or 1
    max_speed = max((s['speed_mph'] for s in samples), default=1) or 1
    max_elevation = max((s['elevation_ft'] for s in samples), default=1) or 1
    max_wind = max((abs(s['headwind_mph']) for s in samples if s.get('headwind_mph') is not None), default=1) or 1
    def path_for(key, maximum, baseline=chart_h):
        return ' '.join(f"{(s['distance_mi'] / max_distance) * chart_w:.1f},{baseline - (float(s.get(key) or 0) / maximum) * (chart_h - 20):.1f}" for s in samples)
    chart = {
        'speed_path': path_for('speed_mph', max_speed),
        'elevation_path': path_for('elevation_ft', max_elevation),
        'wind_path': path_for('headwind_mph', max_wind, chart_h / 2),
        'anomalies': [{'x': round((s['distance_mi'] / max_distance) * chart_w, 1),
                      'y': round(chart_h - (float(s.get('speed_mph') or 0) / max_speed) * (chart_h - 20), 1),
                      'label': s['anomaly']} for s in samples if s.get('anomaly')],
    }
    return {'route': route_json, 'track': [[p['lat'], p['lng']] for p in track], 'samples': samples,
            'segments': segment_rows, 'chart': chart, 'wind_available': bool(winds)}


def operator_required(view):
    """Require the separate national-operations session."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('brevethub_operator'):
            return redirect(url_for('admin.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        configured = current_app.config.get('ADMIN_PASSWORD')
        supplied = request.form.get('password') or ''
        if configured and hmac.compare_digest(supplied, configured):
            session['brevethub_operator'] = True
            return redirect(request.args.get('next') or url_for('admin.dashboard'))
        flash('Incorrect admin password.', 'error')
    return render_template('admin_login.html')


@admin_bp.route('/logout', methods=['POST'])
def logout():
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
        run_warm_brevet_plans,
        run_warm_brevet_route_weather,
        run_warm_plan_elevation,
    )
    operations = {
        'refresh-calendar': run_refresh_calendar,
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
