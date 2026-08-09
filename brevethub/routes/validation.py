"""Rider-owned brevet evidence submission.

Organizers review submissions, but riders create them.  Every route below is
scoped from the signed-in session; event and rider ids are never trusted from a
form field.  The parsing and advisory checks are shared with the operator queue
so a rider submission produces exactly the same evidence/check records.
"""
import json
import re
import zlib
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from brevethub import models
from brevethub.decorators import current_rider, profile_required
from brevethub.services.ride_validation import (
    TrackPoint, combine_recordings, fingerprint, parse_fit, parse_gpx,
    validate_submission,
)
from brevethub.routes.strava import load_strava_section
from brevethub.routes.strava import _valid_access_token
from shared.strava import fetch_activity_streams
from brevethub.shared.strava_analysis import _compress_streams, build_activity_analysis
from brevethub.shared.rwgps import extract_rwgps_route_id

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


def _fetch_selected_strava_recording(rider, activity_id, activity, event):
    """Return GPS points for a selected, rider-owned Strava activity.

    The evidence picker is intentionally independent of the ride-analysis page:
    a rider should not have to open "Analyze" first.  Reuse the same stream
    fetch/cache path, then return points timestamped from the activity's actual
    start (falling back to the official start for old cache rows that predate
    start-time retention).
    """
    cached = models.get_ride_analysis(rider['id'], int(activity_id))
    started_at = (activity or {}).get('start_date') or (activity or {}).get('start_date_local')
    if not started_at:
        started_at = _official_start(event).isoformat()
    if cached and cached.get('activity_streams'):
        return _points_from_strava(cached, started_at), cached

    connection = models.get_strava_connection(rider['id'])
    if not connection:
        raise ValueError('Connect Strava before selecting a Strava activity as evidence.')
    token = _valid_access_token(rider['id'], connection)
    streams = fetch_activity_streams(
        token, int(activity_id), api_base=current_app.config['STRAVA_API_BASE'])
    # Cache the raw stream so the validation queue, future re-submissions, and
    # analysis page all see the same immutable source without another API call.
    analysis = build_activity_analysis(streams, activity or {})
    models.upsert_ride_analysis(
        rider['id'], int(activity_id), analysis,
        compressed_streams=_compress_streams(streams))
    return _points_from_strava({'activity_streams': _compress_streams(streams)}, started_at), {
        'activity_streams': _compress_streams(streams),
    }


def _render_form(event, *, status=200, values=None):
    rider = current_rider()
    strava = load_strava_section(rider) if rider else {'connected': False}
    stats = strava.get('stats') or {}
    activities = stats.get('evidence_activities') or stats.get('activities') or []
    activities = [a for a in activities if a.get('activity_type') in ('Ride', 'EBikeRide')]
    selected_id = str((values or {}).get('strava_activity_id') or '')
    if not selected_id and activities:
        target_distance = float(event.get('distance_km') or 0)
        def score(activity):
            day = str(activity.get('start_date_local') or '')[:10]
            try:
                date_penalty = abs((datetime.fromisoformat(day).date() - event['date']).days)
            except (ValueError, TypeError, KeyError):
                date_penalty = 9999
            return (date_penalty, abs((float(activity.get('distance') or 0) / 1000) - target_distance))
        selected_id = str(min(activities, key=score).get('strava_activity_id') or '')
    plan = models.get_brevet_route_plan_with_stops(event['id']) or {'stops': []}
    return render_template('validation_submit.html', event=event, values=values or {},
                           strava_activities=activities, selected_strava_activity_id=selected_id,
                           controls=plan.get('stops') or []), status


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
    strava_url = (request.form.get('strava_url') or '').strip()
    if strava_url and not strava_id:
        match = re.search(r'(?:activities?/|activity/|^)(\d{6,})', strava_url)
        if match:
            strava_id = match.group(1)
        else:
            flash('Enter a Strava activity URL containing its numeric activity id.', 'error')
            return _render_form(event, 400, request.form)
    if strava_id:
        try:
            official = _official_start(event)
            if not official:
                raise ValueError('This brevet has no official start time yet.')
            stats_activities = (load_strava_section(rider).get('stats') or {}).get('evidence_activities') or []
            activity = next((a for a in stats_activities
                             if str(a.get('strava_activity_id')) == str(strava_id)), None)
            if activity is None:
                # URL submissions may refer to an older activity that is not in
                # the one-year picker. It is still safe to retain the pointer;
                # the organizer can request additional proof rather than letting
                # an unverified activity through the ownership gate.
                metadata['strava_stream_pending'] = True
            else:
                points, _ = _fetch_selected_strava_recording(
                    rider, int(strava_id), activity, event)
                recordings.append(points)
                metadata['strava_stream_fetched'] = True
                metadata['strava_activity_name'] = activity.get('name')
                metadata['strava_activity_started_at'] = activity.get('start_date') or activity.get('start_date_local')
            metadata['strava_activity_url'] = strava_url or f'https://www.strava.com/activities/{strava_id}'
        except Exception as exc:
            # Preserve the submission pointer when Strava is temporarily
            # unavailable. The organizer sees the linked activity and can ask
            # for a FIT/GPX or traditional proof instead of losing the entry.
            current_app.logger.warning('Strava evidence fetch failed for rider %s activity %s: %s', rider['id'], strava_id, exc)
            metadata['strava_stream_pending'] = True
            metadata['strava_fetch_error'] = str(exc)
            metadata['strava_activity_url'] = strava_url or f'https://www.strava.com/activities/{strava_id}'
        metadata.update({'format': 'strava_stream', 'source': 'Strava'})

    metadata.update({
        'device': (request.form.get('source_device') or metadata.get('device') or '').strip() or None,
        # BrevetHub evidence is a human-powered brevet submission by definition;
        # synthetic/manual provenance is still recorded by the parser metadata.
        'activity_type': 'Ride',
        'manual': False,
        'recording_count': len(recordings),
    })
    evidence_orders = set()
    raw_orders = request.form.getlist('control_evidence_orders') or (request.form.get('control_evidence_orders') or '').split(',')
    for raw in raw_orders:
        if raw.strip():
            try:
                evidence_orders.add(int(raw.strip()))
            except ValueError:
                flash('Control proof orders must be comma-separated whole numbers.', 'error')
                return _render_form(event, 400, request.form)
    route_plan = models.get_brevet_route_plan_with_stops(event_id) or {'plan': {}, 'stops': []}
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
    # Per-control traditional proof is the primary organizer-friendly shape. Each
    # control can carry its own receipt/photo and/or a short note; the selected
    # control orders are inferred from whichever row has evidence.
    per_control_notes = []
    for control in (route_plan.get('stops') or []):
        if control.get('stop_type') in ('start', 'finish'):
            continue
        order = str(control.get('stop_order'))
        row_description = (request.form.get(f'proof_description_{order}') or '').strip()
        row_files = request.files.getlist(f'proof_files_{order}')
        if row_description or any(f and f.filename for f in row_files):
            try:
                evidence_orders.add(int(order))
            except ValueError:
                pass
        if row_description:
            traditional = True
            per_control_notes.append(f"Control {order}: {row_description}")
        for uploaded in row_files:
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
    if per_control_notes:
        proof_description = '\n'.join(per_control_notes + ([proof_description] if proof_description else []))
    traditional = traditional or bool(proof_description)
    if not recordings and not traditional and not strava_id:
        flash('Add a FIT/GPX recording, an analyzed Strava activity, or traditional proof before submitting.', 'error')
        return _render_form(event, 400, request.form)

    points = combine_recordings(recordings) if recordings else []
    route_id = (route_plan.get('plan') or {}).get('rwgps_route_id')
    route = models.get_rp_route_elevation_track(route_id) if route_id else []
    # A copied/derived plan can reference a different RWGPS route id than the
    # official event URL. Prefer warmed official geometry when the plan copy has
    # not been warmed yet; otherwise route checks falsely report no geometry.
    if not route:
        official_route_id = extract_rwgps_route_id(event.get('rwgps_url'))
        if official_route_id and str(official_route_id) != str(route_id):
            route = models.get_rp_route_elevation_track(official_route_id) or []
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
