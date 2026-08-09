"""SFR event sheet enrichment for BrevetHub registration metadata.

Ports the SFR Google Spreadsheet parser from scripts/update_rusa_events.py so
BrevetHub can populate start times, fees, deadlines, and capacity on rp_brevet_event
without importing Team Asha scripts.
"""
from __future__ import annotations

import csv
import re
from io import StringIO
from urllib.request import urlopen

SFR_SHEET_URL = (
    'https://docs.google.com/spreadsheets/d/'
    '1LO6FfMJeMP_cvnEUtCfBvpmVudLNzWH-dRVv_PWqLqQ/export?format=csv&gid=0'
)
SFR_REGION = 'CA: San Francisco'


def _parse_distance_km(event_name: str) -> int | None:
    for part in (event_name or '').split():
        if 'k' in part.lower() and part.lower() != 'k':
            try:
                return int(part.lower().replace('k', ''))
            except ValueError:
                continue
    return None


def _parse_time_limit_hours(raw: str) -> float | None:
    if not raw or 'hrs' not in raw.lower():
        return None
    try:
        return float(raw.lower().replace('hrs', '').strip())
    except ValueError:
        return None


def _parse_fee_cents(raw: str) -> int | None:
    if not raw:
        return None
    digits = re.sub(r'[^\d.]', '', raw)
    if not digits:
        return None
    try:
        return int(round(float(digits) * 100))
    except ValueError:
        return None


def _parse_deadline(raw: str) -> str | None:
    """Return ISO date string YYYY-MM-DD or None."""
    raw = (raw or '').strip()
    if not raw or raw.upper() in ('TBD', 'N/A', '—'):
        return None
    # Common sheet formats: "Aug 22", "2026-08-22", "8/22/2026"
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%b %d', '%B %d'):
        try:
            from datetime import datetime
            dt = datetime.strptime(raw, fmt)
            if dt.year < 2000:
                dt = dt.replace(year=datetime.now().year)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def download_sfr_events(sheet_url: str = SFR_SHEET_URL) -> list[dict]:
    """Download and parse SFR events from the public Google Sheet CSV export."""
    try:
        response = urlopen(sheet_url, timeout=15)
        csv_data = response.read().decode('utf-8')
    except Exception:
        return []

    if csv_data.strip().startswith('<'):
        return []

    rows = list(csv.reader(StringIO(csv_data)))
    if len(rows) < 2:
        return []

    header = rows[1]
    col_map = {name: idx for idx, name in enumerate(header)}
    events = []

    for row in rows[2:]:
        if len(row) < 5:
            continue
        try:
            event_date = row[col_map.get('Event date', 0)].strip()
            event_name = row[col_map.get('Event', 1)].strip()
            start_time = row[col_map.get('Start time', 2)].strip()
            time_limit = row[col_map.get('Time limit', 3)].strip()
            rwgps_url = row[col_map.get('RideWithGPS link', 4)].strip()
            fee_raw = row[col_map.get('Fee', 5)].strip() if 'Fee' in col_map else ''
            deadline_raw = row[col_map.get('Registration deadline', 8)].strip() if 'Registration deadline' in col_map else ''
            capacity_raw = row[col_map.get('Capacity', 10)].strip() if 'Capacity' in col_map else ''
            elevation_ft = row[col_map.get('Elev. gain (ft)', 7)].strip()
            start_location = row[col_map.get('Start/finish location', 9)].strip()

            if not event_date or not event_name:
                continue

            distance_km = _parse_distance_km(event_name)
            if not distance_km:
                continue

            csv_elevation = None
            if elevation_ft:
                try:
                    csv_elevation = int(elevation_ft.replace(',', '').replace("'", ''))
                except (ValueError, AttributeError):
                    pass

            capacity = None
            if capacity_raw:
                try:
                    capacity = int(re.sub(r'[^\d]', '', capacity_raw))
                except ValueError:
                    capacity = None

            events.append({
                'date': event_date,
                'name': event_name,
                'distance_km': distance_km,
                'elevation_ft': csv_elevation,
                'rwgps_url': rwgps_url if rwgps_url and rwgps_url.lower() not in ('n/a', 'coming soon', 'tbd') else None,
                'start_time': start_time if start_time and start_time.upper() != 'TBD' else None,
                'time_limit_hours': _parse_time_limit_hours(time_limit),
                'start_location': start_location or None,
                'region': SFR_REGION,
                'fee_cents': _parse_fee_cents(fee_raw),
                'registration_deadline': _parse_deadline(deadline_raw),
                'capacity': capacity,
            })
        except (IndexError, ValueError):
            continue
    return events
