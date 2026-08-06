"""Calendar-day and event-timezone helpers for club rides."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


CLUB_TIMEZONE = ZoneInfo("America/Los_Angeles")
PACIFIC_TIMEZONE = CLUB_TIMEZONE

_CENTRAL = {'alabama', 'arkansas', 'illinois', 'iowa', 'kansas', 'louisiana',
            'minnesota', 'mississippi', 'missouri', 'nebraska', 'north dakota',
            'oklahoma', 'south dakota', 'tennessee', 'texas', 'wisconsin'}
_MOUNTAIN = {'colorado', 'montana', 'new mexico', 'utah', 'wyoming'}
_EASTERN = {'connecticut', 'delaware', 'florida', 'georgia', 'indiana', 'kentucky',
            'maine', 'maryland', 'massachusetts', 'michigan', 'new hampshire',
            'new jersey', 'new york', 'north carolina', 'ohio', 'pennsylvania',
            'rhode island', 'south carolina', 'vermont', 'virginia',
            'washington dc', 'west virginia'}
_ALASKA = {'alaska'}
_HAWAII = {'hawaii'}


def ride_timezone(ride):
    """Best available event timezone from the ride's club region/state."""
    timezone_key = str((ride or {}).get('timezone') or '').strip()
    if timezone_key:
        try:
            return ZoneInfo(timezone_key)
        except (KeyError, ValueError):
            pass
    region = str((ride or {}).get('region') or '').strip().lower()
    if region in _CENTRAL:
        return ZoneInfo('America/Chicago')
    if region in _MOUNTAIN:
        return ZoneInfo('America/Denver')
    if region in _EASTERN:
        return ZoneInfo('America/New_York')
    if region in _ALASKA:
        return ZoneInfo('America/Anchorage')
    if region in _HAWAII:
        return ZoneInfo('Pacific/Honolulu')
    return CLUB_TIMEZONE


def timezone_label(tz):
    """Stable audience-friendly zone label (PT/MT/CT/ET), not DST jargon."""
    key = getattr(tz, 'key', str(tz))
    return {
        'America/Los_Angeles': 'PT', 'America/Denver': 'MT',
        'America/Chicago': 'CT', 'America/New_York': 'ET',
        'America/Anchorage': 'AKT', 'Pacific/Honolulu': 'HT',
    }.get(key, key)


def schedule_time_labels(ride, start_time, elapsed_min, *, twelve_hour=False):
    """Return event-local primary and Pacific secondary schedule clocks."""
    event_tz = ride_timezone(ride)
    ride_date = (ride or {}).get('date') or datetime.now(event_tz).date()
    if isinstance(ride_date, str):
        ride_date = date.fromisoformat(ride_date)
    try:
        hh, mm = (int(v) for v in str(start_time or '06:00').split(':')[:2])
    except (TypeError, ValueError):
        hh, mm = 6, 0
    start = datetime(ride_date.year, ride_date.month, ride_date.day, hh, mm,
                     tzinfo=event_tz)
    event_dt = start + timedelta(minutes=float(elapsed_min or 0))
    pacific_dt = event_dt.astimezone(PACIFIC_TIMEZONE)

    def clock(value, base_date):
        rendered = (value.strftime('%I:%M %p').lstrip('0') if twelve_hour
                    else value.strftime('%H:%M'))
        day_offset = (value.date() - base_date).days
        return f'{rendered}+{day_offset}' if day_offset > 0 else rendered

    pacific_start_date = start.astimezone(PACIFIC_TIMEZONE).date()
    return {
        'event': clock(event_dt, start.date()),
        'event_zone': timezone_label(event_tz),
        'pacific': clock(pacific_dt, pacific_start_date),
        'show_pacific': event_tz != PACIFIC_TIMEZONE,
    }


def instant_time_labels(value, ride):
    """Format one aware instant in the event timezone and Pacific timezone."""
    event_tz = ride_timezone(ride)
    dt = value if value.tzinfo else value.replace(tzinfo=event_tz)
    event = dt.astimezone(event_tz).strftime('%I:%M %p').lstrip('0')
    pacific = dt.astimezone(PACIFIC_TIMEZONE).strftime('%I:%M %p').lstrip('0')
    return {
        'event': event, 'event_zone': timezone_label(event_tz),
        'pacific': pacific, 'show_pacific': event_tz != PACIFIC_TIMEZONE,
    }


def club_today(now=None):
    """Return today's date in the timezone used by Team Asha ride schedules."""
    current = now or datetime.now(CLUB_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CLUB_TIMEZONE)
    return current.astimezone(CLUB_TIMEZONE).date()
