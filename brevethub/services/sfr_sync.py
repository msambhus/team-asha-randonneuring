"""Apply SFR spreadsheet metadata onto cached rp_brevet_event rows."""
from __future__ import annotations

from datetime import datetime

from brevethub import models
from brevethub.services.sfr_events import download_sfr_events


def _parse_sheet_date(raw: str):
    raw = (raw or '').strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%b %d, %Y', '%B %d, %Y', '%b %d', '%B %d'):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year < 2000:
                dt = dt.replace(year=datetime.now().year)
            return dt.date()
        except ValueError:
            continue
    return None


def sync_sfr_registration_metadata(*, enable_registration: bool = True) -> dict:
    """Match SFR sheet rows to rp_brevet_event and merge registration fields."""
    sfr_club = models.get_club_by_rusa_code('SFR')
    club_id = sfr_club['id'] if sfr_club else None
    sheet_events = download_sfr_events()
    matched = 0
    for ev in sheet_events:
        event_date = _parse_sheet_date(ev.get('date'))
        if not event_date:
            continue
        row = models.find_brevet_event_by_key(event_date, ev['name'], ev['distance_km'])
        if not row:
            continue
        models.enrich_brevet_event_registration(
            row['id'],
            start_time=ev.get('start_time'),
            start_location=ev.get('start_location'),
            fee_cents=ev.get('fee_cents'),
            registration_deadline=ev.get('registration_deadline'),
            capacity=ev.get('capacity'),
            elevation_ft=ev.get('elevation_ft'),
            rwgps_url=ev.get('rwgps_url'),
            time_limit_hours=ev.get('time_limit_hours'),
            club_id=club_id,
            registration_enabled=enable_registration,
        )
        matched += 1
    return {'sheet_events': len(sheet_events), 'matched': matched}
