"""Calendar-day helpers for Bay Area club events."""
from datetime import datetime
from zoneinfo import ZoneInfo


CLUB_TIMEZONE = ZoneInfo("America/Los_Angeles")

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


def club_today(now=None):
    """Return today's date in the timezone used by Team Asha ride schedules."""
    current = now or datetime.now(CLUB_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CLUB_TIMEZONE)
    return current.astimezone(CLUB_TIMEZONE).date()
