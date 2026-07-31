"""Calendar-day helpers for Bay Area club events."""
from datetime import datetime
from zoneinfo import ZoneInfo


CLUB_TIMEZONE = ZoneInfo("America/Los_Angeles")


def club_today(now=None):
    """Return today's date in the timezone used by Team Asha ride schedules."""
    current = now or datetime.now(CLUB_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CLUB_TIMEZONE)
    return current.astimezone(CLUB_TIMEZONE).date()
