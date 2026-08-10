"""Per-deployment club home page — one BrevetHub instance, one club."""
from datetime import date

from shared import seasons
from shared.calendar_view import MONTH_ABBR, calendar_event, event_category

from brevethub import models
from brevethub.services.registration import (
    existing_registration_payload,
    rider_already_registered,
)


def host_club_from_config(app):
    """Return the configured host club row + display fields, or None."""
    club_id = app.config.get('HOST_CLUB_ID')
    if not club_id:
        return None
    club = models.get_club(club_id)
    if not club:
        return None
    city = (club.get('city') or '').strip()
    name = club['name']
    abbrev = (app.config.get('HOST_CLUB_ABBREV') or '').strip()
    if not abbrev:
        parts = [p for p in name.replace('-', ' ').split() if p and p[0].isalpha()]
        abbrev = ''.join(p[0].upper() for p in parts[:3]) or 'CLB'

    region_prefix = (app.config.get('HOST_REGION_PREFIX') or '').strip() or None
    filter_state = None
    filter_area = None
    if region_prefix and ':' in region_prefix:
        filter_state, _, filter_area = region_prefix.partition(':')
        filter_state = filter_state.strip() or None
        filter_area = filter_area.strip() or None
    locale = city or (club.get('state') or '').strip() or 'your region'

    return {
        'id': club['id'],
        'name': name,
        'city': city,
        'state': club.get('state'),
        'abbrev': abbrev,
        'region_prefix': region_prefix,
        'filter_state': filter_state,
        'filter_area': filter_area,
        'hero_headline': (
            app.config.get('HOST_HERO_HEADLINE')
            or f'Long rides out of {locale}, all year.'
        ),
        'hero_body': (
            app.config.get('HOST_HERO_BODY')
            or ('Brevets from 100 to 1,200 km — self-supported, on the clock, '
                'under ACP and RUSA rules. New riders welcome on any distance.')
        ),
        'new_rider_guide_url': (app.config.get('HOST_NEW_RIDER_GUIDE_URL') or '').strip(),
        'about_url': (app.config.get('HOST_ABOUT_URL') or '').strip(),
    }


def _events_for_host(host_club):
    """Upcoming brevets for this deployment, optionally scoped to the club region."""
    state = None
    prefix = host_club.get('region_prefix') if host_club else None
    if prefix and ':' in prefix:
        state = prefix.split(':', 1)[0].strip()
    rows = models.get_upcoming_events(state=state, limit=200)
    events = [calendar_event(row) for row in rows]
    if prefix:
        events = [ev for ev in events if (ev.get('region') or '') == prefix]
    return events


def _format_short_date(value):
    if not value:
        return '—'
    iso = str(value)[:10]
    try:
        year, month, day = (int(iso[0:4]), int(iso[5:7]), int(iso[8:10]))
        return f'{day} {MONTH_ABBR[month]}'
    except (ValueError, IndexError):
        return iso


def _format_weekday_time(event):
    iso = str(event.get('date') or '')[:10]
    start = (event.get('start_time') or '').strip()
    if not iso:
        return start or '—'
    try:
        dt = date.fromisoformat(iso)
        weekday = dt.strftime('%a')
        if start:
            return f'{weekday} {dt.day} {MONTH_ABBR[dt.month]} · {start}'
        return f'{weekday} {dt.day} {MONTH_ABBR[dt.month]}'
    except ValueError:
        return start or iso


def _schedule_badge(event):
    cat = event_category(event.get('ride_type'))
    if cat == 'fleche':
        return {'label': 'Teams forming', 'tone': 'muted'}
    distance = event.get('distance_km') or 0
    if cat == 'populaire' or distance <= 120:
        return {'label': 'Good first brevet', 'tone': 'good'}
    if event.get('registration_enabled'):
        count = int(event.get('signup_count') or 0)
        label = f'Open · {count} signed up' if count else 'Open'
        return {'label': label, 'tone': 'open'}
    return {'label': 'On calendar', 'tone': 'muted'}


def _event_type_label(event):
    cat = event_category(event.get('ride_type'))
    labels = {
        'acp_brevet': 'ACP brevet',
        'rusa_brevet': 'RUSA brevet',
        'populaire': 'Populaire',
        'fleche': 'Team event',
        'team': 'Team event',
    }
    return labels.get(cat, 'Brevet')


def _event_date(value):
    """Normalize an event date to ``date`` for comparisons."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    if hasattr(value, 'date'):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _riding_today(events):
    """Brevets scheduled for today, with an optional public live ride link."""
    today = date.today()
    riding = []
    for ev in events:
        if _event_date(ev.get('date')) != today:
            continue
        live_ids = models.get_live_ride_ids_for_event(ev['id'])
        riding.append({
            'id': ev['id'],
            'name': ev.get('name') or 'Today\'s brevet',
            'distance_km': ev.get('distance_km'),
            'live_ride_id': live_ids[0] if live_ids else None,
        })
    return riding


def _registration_countdown(event):
    """Registration deadline metadata for the featured-card live countdown."""
    if not event.get('registration_enabled'):
        return None
    today = date.today()
    deadline = _event_date(event.get('registration_deadline'))
    if deadline:
        days_left = (deadline - today).days
        if days_left < 0:
            return {'tone': 'closed', 'deadline_iso': None, 'deadline_display': None}
        tone = 'urgent' if days_left <= 7 else 'open'
        return {
            'tone': tone,
            'deadline_iso': deadline.isoformat(),
            'deadline_display': _format_short_date(deadline),
        }
    return None


def _club_stats(club_id):
    rows = models.get_club_riders_with_rusa(club_id)
    today = date.today()
    total_km = 0
    sr_series = 0
    pbp_anciens = 0
    for row in rows:
        brevets = row.get('rusa_cache') or []
        career = seasons.career_summary(brevets, today)
        total_km += career['total_km']
        sr_series += career['total_sr']
        pbp_anciens += len(seasons.pbp_ancien_years(brevets))
    return {
        'active_riders': len(rows),
        'sr_series': sr_series,
        'club_km': total_km,
        'pbp_anciens': pbp_anciens,
    }


def build_club_home_context(host_club, rider_id=None):
    """Assemble template context for the club landing page."""
    events = _events_for_host(host_club)
    schedule = []
    for ev in events[:6]:
        schedule.append({
            **ev,
            'short_date': _format_short_date(ev.get('date')),
            'badge': _schedule_badge(ev),
            'type_label': _event_type_label(ev),
        })

    featured = schedule[0] if schedule else None
    stats = _club_stats(host_club['id']) if host_club.get('id') else {}
    riding_today = _riding_today(events)
    season_name = seasons.current_season_name(date.today())
    events_left = len(events)

    if featured:
        fee = featured.get('fee_cents')
        featured_card = {
            'name': featured.get('name') or 'Upcoming brevet',
            'when_line': _format_weekday_time(featured),
            'location': featured.get('start_location') or 'Start TBA',
            'distance_km': featured.get('distance_km'),
            'time_limit_hours': featured.get('time_limit_hours'),
            'fee_display': f'${fee // 100}' if fee else None,
            'event_id': featured.get('id'),
            'registration_enabled': featured.get('registration_enabled'),
            'register_countdown': _registration_countdown(featured),
        }
        if rider_id and featured.get('id'):
            existing = models.get_event_signup_registration(rider_id, featured['id'])
            featured_card['already_registered'] = rider_already_registered(existing)
            if featured_card['already_registered']:
                featured_card['registration'] = existing_registration_payload(existing)
    else:
        featured_card = None

    return {
        'host_club': host_club,
        'season_name': season_name,
        'events_left': events_left,
        'featured': featured_card,
        'schedule': schedule,
        'stats': stats,
        'riding_today': riding_today,
        'calendar_year': date.today().year,
    }
