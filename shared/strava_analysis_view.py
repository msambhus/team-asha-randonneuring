"""Shared Team Asha Strava-analysis presentation contract.

The Team Asha template is the canonical UX for both products.  Data ownership
remains product-specific: callers supply an already-authorized, normalized
analysis payload and the current rider identifier.  This module only adapts that
payload to the template contract; it performs no database, session, or Strava
access.
"""

MILES_TO_KM = 1.609344
MPH_TO_KMH = 1.609344


def _parse_duration_minutes(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(value)
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        if 'h' in text:
            hour_text, rest = text.split('h', 1)
            minutes = ''.join(ch for ch in rest if ch.isdigit())
            return int(hour_text.strip()) * 60 + (int(minutes) if minutes else 0)
        if text.endswith('m'):
            return int(''.join(ch for ch in text if ch.isdigit()))
    except (TypeError, ValueError):
        return None
    return None


def _summary(analysis):
    activity = analysis.get('activity') or {}
    comparison = analysis.get('comparison') or {}
    source = comparison.get('summary') or {}

    actual_km = source.get('actual_distance_km', activity.get('distance_km') or 0)
    plan_km = source.get('plan_distance_km')
    actual_miles = round((actual_km or 0) / MILES_TO_KM, 1)
    plan_miles = round(plan_km / MILES_TO_KM, 1) if plan_km else None
    elapsed_min = source.get('actual_elapsed_time_min')
    if elapsed_min is None:
        elapsed_min = _parse_duration_minutes(activity.get('elapsed_time'))
    moving_min = source.get('actual_moving_time_min')
    if moving_min is None:
        moving_min = _parse_duration_minutes(activity.get('moving_time'))
    stopped_min = source.get('actual_stopped_time_min')
    if stopped_min is None and elapsed_min is not None and moving_min is not None:
        stopped_min = max(0, elapsed_min - moving_min)

    plan_total = source.get('plan_total_time_min')
    plan_break = source.get('plan_break_time_min')
    actual_avg_mph = None
    if elapsed_min and actual_miles:
        actual_avg_mph = round(actual_miles / (elapsed_min / 60), 1)
    elif activity.get('avg_speed_kmh') is not None:
        actual_avg_mph = round(activity['avg_speed_kmh'] / MPH_TO_KMH, 1)
    plan_avg_mph = (
        round(plan_miles / (plan_total / 60), 1)
        if plan_miles and plan_total else None
    )

    return {
        'plan_distance_miles': plan_miles,
        'actual_distance_miles': actual_miles,
        'distance_delta_miles': (
            round((source.get('distance_delta_km') or 0) / MILES_TO_KM, 1)
            if source.get('distance_delta_km') is not None else None
        ),
        'plan_elevation_ft': source.get('plan_elevation_ft'),
        'actual_elevation_ft': source.get(
            'actual_elevation_ft', activity.get('elevation_ft')),
        'elevation_delta_ft': (
            source.get('actual_elevation_ft') - source.get('plan_elevation_ft')
            if source.get('actual_elevation_ft') is not None
            and source.get('plan_elevation_ft') is not None else None
        ),
        'plan_total_time_min': plan_total,
        'base_total_time_min': None,
        'actual_elapsed_time_min': elapsed_min or 0,
        'time_delta_min': (
            round((elapsed_min or 0) - plan_total)
            if elapsed_min is not None and plan_total is not None else None
        ),
        'actual_moving_time_min': moving_min or 0,
        'plan_break_time_min': plan_break,
        'actual_stopped_time_min': stopped_min or 0,
        'break_delta_min': (
            round((stopped_min or 0) - plan_break)
            if stopped_min is not None and plan_break is not None else None
        ),
        'plan_avg_speed_mph': plan_avg_mph,
        'actual_avg_speed_mph': actual_avg_mph,
        'speed_delta_mph': (
            round(actual_avg_mph - plan_avg_mph, 1)
            if actual_avg_mph is not None and plan_avg_mph is not None else None
        ),
        'stops_detected': source.get(
            'stops_detected', analysis.get('stop_count') or 0),
        'stops_planned': source.get('stops_planned'),
        'stops_extra': source.get('stops_extra', 0),
    }


def _rows(analysis):
    rows = []
    for row in (analysis.get('comparison') or {}).get('rows') or []:
        item = dict(row)
        item.setdefault('stop_type', 'extra' if item.get('is_extra') else 'waypoint')
        item.setdefault('custom', None)
        item.setdefault('actual_seg_break_min', None)
        item.setdefault('actual_np_watts', item.get('np_watts'))
        if item.get('actual_climb_ft_per_mi') is None:
            miles = item.get('distance_miles') or 0
            gain = item.get('actual_elev_gain_ft')
            item['actual_climb_ft_per_mi'] = (
                round(gain / miles) if gain is not None and miles else None
            )
        for key in (
            'plan_arrival_time_min', 'actual_arrival_time_min',
            'plan_time_of_day', 'actual_time_of_day', 'plan_time_bank',
            'actual_time_bank', 'plan_segment_min', 'actual_segment_min',
            'plan_speed_mph', 'actual_speed_mph', 'plan_stop_duration_min',
            'actual_stop_duration_min', 'plan_cum_time_min',
            'actual_cum_time_min', 'actual_avg_hr', 'actual_avg_watts',
            'actual_avg_cadence', 'actual_elev_gain_ft', 'cum_time_delta_min',
        ):
            item.setdefault(key, None)
        rows.append(item)
    return rows


def _hr_power(analysis, rows):
    summary = analysis.get('summary') or {}
    values = {
        'avg_hr': summary.get('avg_hr'),
        'max_hr': summary.get('max_hr'),
        'avg_watts': summary.get('avg_watts'),
        'weighted_avg_watts': summary.get('np_watts'),
        'max_watts': summary.get('max_watts'),
        'kilojoules': summary.get('kilojoules'),
        'suffer_score': summary.get('suffer_score'),
    }
    if any(value is not None for value in values.values()):
        return values
    if any(row.get('actual_avg_hr') or row.get('actual_avg_watts') for row in rows):
        return values
    return None


def _comparison(analysis):
    rows = _rows(analysis)
    return {
        'summary': _summary(analysis),
        'rows': rows,
        'hr_power': _hr_power(analysis, rows),
    }


def _map_data(analysis):
    source = dict(analysis.get('map') or {})
    if not source.get('track'):
        return None
    stops = []
    for stop in source.get('stops') or analysis.get('stops') or []:
        item = dict(stop)
        if item.get('distance_miles') is None and item.get('distance_km') is not None:
            item['distance_miles'] = round(item['distance_km'] / MILES_TO_KM, 1)
        stops.append(item)
    source['stops'] = stops
    source.setdefault('segments', [])
    return source


def build_team_asha_analysis_context(
        analysis, activity_id, rider_id, stop_wind_by_location=None):
    """Adapt one product-owned analysis to the canonical Team Asha template."""
    activity = analysis.get('activity') or {}
    notes = analysis.get('notes') or {}
    return {
        'ride': {
            'id': activity_id,
            'name': activity.get('name') or 'Strava ride',
            'date': activity.get('date'),
            'distance_km': activity.get('distance_km'),
        },
        'rider': {'rusa_id': rider_id or 0},
        'activity': {
            'strava_url': (
                activity.get('strava_url') or
                f'https://www.strava.com/activities/{activity_id}'
            ),
        },
        'comparison': _comparison(analysis),
        'map_data': _map_data(analysis),
        'stop_wind': stop_wind_by_location or None,
        'has_custom': False,
        'has_plan': bool((analysis.get('comparison') or {}).get('rows')),
        'plan_slug': None,
        'error': None,
        'is_own_profile': True,
        'segment_eval': {},
        'ride_recommendations': None,
        'overall_narrative': [],
        'overall_note': notes.get('overall') or '',
        'segment_notes': notes.get('segments') or {},
        'stop_notes': notes.get('stops') or {},
    }
