"""Shared calendar view-model contracts for Team Asha and BrevetHub.

Repositories remain product-owned. These helpers make a lossless copy of each
record and supply the common fields expected by calendar and finisher surfaces.
"""

MONTH_ABBR = [
    '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]


_EVENT_DEFAULTS = {
    'id': None,
    'name': '',
    'route_name': None,
    'date': None,
    'date_str': None,
    'distance_km': None,
    'distance_miles': None,
    'elevation_ft': None,
    'ride_type': None,
    'region': None,
    'club_id': None,
    'club_code': None,
    'club_name': None,
    'club_state': None,
    'start_location': None,
    'start_time': None,
    'time_limit_hours': None,
    'rwgps_url': None,
    'rwgps_url_team': None,
    'ride_plan_id': None,
    'plan_slug': None,
    'plan_rwgps_url': None,
    'plan_rwgps_url_team': None,
    'plan_start_time': None,
    'plan_avg_speed': None,
    'signup_count': 0,
    'interested_count': 0,
    'has_custom_plan': False,
    'is_team_ride': False,
}


def calendar_event(record, **overrides):
    """Return a canonical, lossless calendar event dictionary."""
    event = dict(record or {})
    for key, value in _EVENT_DEFAULTS.items():
        event.setdefault(key, value)
    event.update(overrides)
    if event.get('date_str') is None and event.get('date') is not None:
        event['date_str'] = str(event['date'])
    return event


def completed_event(record, **overrides):
    """Return a completed-event row with canonical finisher fields."""
    event = calendar_event(record, **overrides)
    event.setdefault('finisher_count', 0)
    event.setdefault('finishers_url', None)
    return event


def finisher_row(record):
    """Return the public finisher shape shared by both products."""
    row = dict(record or {})
    return {
        **row,
        'rider_id': row.get('rider_id'),
        'rusa_id': row.get('rusa_id'),
        'first_name': row.get('first_name') or '',
        'last_name': row.get('last_name') or '',
        'display_name': (
            row.get('display_name') or
            ' '.join(filter(None, [
                row.get('first_name'), row.get('last_name')])).strip()
        ),
        'finish_time': row.get('finish_time'),
    }


def event_category(ride_type: str | None) -> str:
    """Normalise raw RUSA ride_type into one of: acp_brevet, rusa_brevet, populaire, fleche, team, other."""
    rt = (ride_type or '').lower()
    if 'flèche' in rt or 'fleche' in rt or 'fle' in rt:
        return 'fleche'
    if 'dart' in rt or 'arrow' in rt:
        return 'fleche'  # dart/arrow are team-format events, shown as fleche category
    if 'populaire' in rt or 'popular' in rt:
        return 'populaire'
    if 'acp' in rt:
        return 'acp_brevet'
    if 'rusa' in rt:
        return 'rusa_brevet'
    if 'randonnée' in rt or 'randonnee' in rt or 'uaf' in rt:
        return 'rusa_brevet'
    return 'other'


def month_label(value):
    """Format a date-like value as ``'March 2026'`` for month headers."""
    if value is None:
        return ''
    iso = str(value)
    try:
        from datetime import datetime
        return datetime.strptime(iso[:10], '%Y-%m-%d').strftime('%B %Y')
    except ValueError:
        return iso


def group_events_by_month(events):
    """Group a date-ordered event list into ``[(month_label, [events]), ...]``."""
    groups = []
    current_label = None
    bucket = None
    for ev in events or []:
        label = month_label(ev.get('date'))
        if label != current_label:
            current_label = label
            bucket = []
            groups.append((label, bucket))
        bucket.append(ev)
    return groups
