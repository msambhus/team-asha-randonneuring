"""Rider-owned brevet evidence submission.

Organizers review submissions, but riders create them.  Every route below is
scoped from the signed-in session; event and rider ids are never trusted from a
form field.  The parsing and advisory checks are shared with the operator queue
so a rider submission produces exactly the same evidence/check records.
"""
import json
import zlib
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from brevethub import models
from brevethub.decorators import current_rider, profile_required
from brevethub.services.ride_validation import (
    TrackPoint, combine_recordings, fingerprint, parse_fit, parse_gpx,
    validate_submission,
)

validation_bp = Blueprint('validation', __name__)
_MAX_UPLOAD = 4 * 1024 * 1024
_STATE_ZONES = {
    **{s: 'America/Los_Angeles' for s in ('CA', 'NV', 'OR', 'WA')},
    **{s: 'America/Denver' for s in ('CO', 'ID', 'MT', 'NM', 'UT', 'WY')},
    **{s: 'America/Chicago' for s in ('AL', 'AR', 'IA', 'IL', 'KS', 'LA', 'MN', 'MO', 'MS', 'ND', 'NE', 'OK', 'SD', 'TN', 'TX', 'WI')},
    **{s: 'America/New_York' for s in ('CT', 'DC', 'DE', 'FL', 'GA', 'IN', 'KY', 'MA', 'MD', 'ME', 'MI', 'NC', 'NH', 'NJ', 'NY', 'OH', 'PA', 'RI', 'SC', 'VA', 'VT', 'WV')},
    'AZ': 'America/Phoenix', 'AK': 'America/Anchorage', 'HI': 'Pacific/Honolulu',
}


def _official_start(event, override=None):
    if override:
        value = datetime.fromisoformat(override.replace('Z', '+00:00'))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not event.get('start_time'):
        return None
    parts = str(event['start_time']).split(':')
    zone = _STATE_ZONES.get(str(event.get('region') or '').split(':', 1)[0].strip().upper(), 'UTC')
    return datetime.combine(event['date'], time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0), tzinfo=ZoneInfo(zone))


def _track_json(points):
    cap = max(1, len(points) // 5000)
    return [[round(p.lat, 6), round(p.lng, 6), p.timestamp.isoformat(),
             round(float(p.elevation_m), 1) if p.elevation_m is not None else None]
            for p in points[::cap]]


def _points_from_strava(row, started_at):
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
    points = [TrackPoint(start + timedelta(seconds=float(seconds[i])), float(pair[0]), float(pair[1]),
                         elevation[i] if i < len(elevation) else None)
              for i, pair in enumerate(latlng) if pair and len(pair) >= 2]
    return combine_recordings([points])


def _render_form(event, *, status=200, values=None):
    return render_template('validation_submit.html', event=event, values=values or {}), status


@validation_bp.route('/my/validations')
@profile_required
def my_validations():
    rider = current_rider()
    return render_template('my_validations.html', rider=rider,
                           events=models.get_rider_completed_validation_events(rider['id']))


@validation_bp.route('/my/validations/<int:event_id>/new', methods=['GET', 'POST'])
@profile_required
def submit_validation(event_id):
    rider = current_rider()
    eligible = next((row for row in models.get_rider_completed_validation_events(rider['id'])
                     if int(row['event_id']) == event_id), None)
    if not eligible:
        abort(404)
    event = models.get_brevet_event_full(event_id)
    if request.method == 'GET':
        return _render_form(event)

    recordings, file_rows, metadata = [], [], {}
    total_bytes = 0
    for uploaded in request.files.getlist('recordings'):
        if not uploaded or not uploaded.filename:
            continue
        data = uploaded.read()
        total_bytes += len(data)
        if total_bytes > _MAX_UPLOAD:
            flash('All evidence uploads must total 4 MB or less.', 'error')
            return _render_form(event, 413, request.form)
        suffix = uploaded.filename.rsplit('.', 1)[-1].lower()
        try:
            points, parsed = parse_fit(data) if suffix == 'fit' else parse_gpx(data)
        except Exception as exc:
            flash(f'Could not parse {uploaded.filename}: {exc}', 'error')
            return _render_form(event, 400, request.form)
        recordings.append(points)
        metadata.update({k: v for k, v in parsed.items() if v is not None})
        file_rows.append(('recording', uploaded, data, fingerprint(data)))

    strava_id = (request.form.get('strava_activity_id') or '').strip()
    if strava_id:
        if not request.form.get('activity_started_at'):
            flash('Enter the Strava activity start time with its timezone.', 'error')
            return _render_form(event, 400, request.form)
        try:
            recordings.append(_points_from_strava(models.get_ride_analysis(rider['id'], int(strava_id)), request.form['activity_started_at']))
        except Exception as exc:
            flash(str(exc), 'error')
            return _render_form(event, 400, request.form)
        metadata.update({'format': 'strava_stream', 'source': 'Strava'})

    metadata.update({
        'device': (request.form.get('source_device') or metadata.get('device') or '').strip() or None,
        'activity_type': (request.form.get('activity_type') or metadata.get('activity_type') or '').strip() or None,
        'manual': request.form.get('manual_activity') == '1',
        'recording_count': len(recordings),
    })
    evidence_orders = set()
    for raw in (request.form.get('control_evidence_orders') or '').split(','):
        if raw.strip():
            try:
                evidence_orders.add(int(raw.strip()))
            except ValueError:
                flash('Control proof orders must be comma-separated whole numbers.', 'error')
                return _render_form(event, 400, request.form)
    traditional = False
    for uploaded in request.files.getlist('proof_files'):
        if not uploaded or not uploaded.filename:
            continue
        data = uploaded.read()
        total_bytes += len(data)
        if total_bytes > _MAX_UPLOAD:
            flash('All evidence uploads must total 4 MB or less.', 'error')
            return _render_form(event, 413, request.form)
        traditional = True
        file_rows.append(('traditional', uploaded, data, fingerprint(data)))
    proof_description = (request.form.get('proof_description') or '').strip()
    traditional = traditional or bool(proof_description)
    if not recordings and not traditional:
        flash('Add a FIT/GPX recording, an analyzed Strava activity, or traditional proof before submitting.', 'error')
        return _render_form(event, 400, request.form)

    points = combine_recordings(recordings) if recordings else []
    route_plan = models.get_brevet_route_plan_with_stops(event_id) or {'plan': {}, 'stops': []}
    route_id = (route_plan.get('plan') or {}).get('rwgps_route_id')
    route = models.get_rp_route_elevation_track(route_id) if route_id else []
    hashes = [row[3] for row in file_rows]
    conflicts = models.find_validation_evidence_conflicts(hashes, event_id=event_id, rider_id=rider['id'],
                                                           strava_activity_id=int(strava_id) if strava_id else None)
    decision, checks = validate_submission(
        points=points, route=route or [], controls=route_plan.get('stops') or [], event=dict(event),
        official_start=_official_start(event, request.form.get('official_start')),
        evidence_control_orders=evidence_orders, source_metadata=metadata,
        duplicate_conflicts=[dict(c) for c in conflicts], has_traditional_evidence=traditional,
    )
    source_type = 'mixed' if recordings and traditional else ('traditional' if traditional and not recordings else ('strava' if strava_id and not file_rows else 'file'))
    created = models.create_validation_submission(
        event_id=event_id, rider_id=rider['id'], submitted_by='rider', source_type=source_type,
        strava_activity_id=int(strava_id) if strava_id else None, source_metadata=metadata,
        normalized_track=_track_json(points), rider_explanation=request.form.get('rider_explanation'),
    )
    submission_id = created['id']
    for kind, uploaded, data, digest in file_rows:
        models.add_validation_evidence(submission_id, evidence_kind=kind, filename=uploaded.filename,
                                       content_type=uploaded.content_type, content=data, sha256=digest,
                                       control_orders=sorted(evidence_orders) if kind == 'traditional' else [],
                                       description=proof_description if kind == 'traditional' else None)
    if proof_description and not any(row[0] == 'traditional' for row in file_rows):
        models.add_validation_evidence(submission_id, evidence_kind='traditional',
                                       control_orders=sorted(evidence_orders), description=proof_description)
    models.replace_validation_checks(submission_id, decision, checks)
    flash('Evidence submitted. An organizer will review any flagged items.', 'success')
    return redirect(url_for('validation.my_validations'))
