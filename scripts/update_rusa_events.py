#!/usr/bin/env python3
"""
Script to update ride table with external RUSA event data from various sources.
Run this script to refresh the RUSA calendar events.

Usage:
    python scripts/update_rusa_events.py
"""

import os
import psycopg2
import sys
import csv
import re
from collections import Counter
from pathlib import Path
from urllib.request import urlopen, Request
from io import StringIO
import html
from html.parser import HTMLParser

# Add parent directory to path to import from project
sys.path.insert(0, str(Path(__file__).parent.parent))

# The RUSA national-feed parser now lives in the club-agnostic shared library so
# both Team Asha and BrevetHub parse the same source of truth. This script is a
# thin importer: the national scrape + RWGPS/time-limit helpers come from shared,
# and the local get_rusa_events() below re-applies the team's region filter so
# runtime behavior is byte-for-byte what it was before the extract.
from shared.rusa_calendar import (
    get_time_limit_hours,
    get_rwgps_url_from_route,
    get_rwgps_details,
    get_rusa_events as _get_rusa_events,
)

# Resolved lazily in main() so importing this module's functions never exits.
DATABASE_URL = os.environ.get('DATABASE_URL')

# Google Sheets URL - convert to CSV export URL
SFR_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1LO6FfMJeMP_cvnEUtCfBvpmVudLNzWH-dRVv_PWqLqQ/export?format=csv&gid=0'


# Santa Cruz Randonneurs website
SCR_EVENTS_URL = 'https://santacruzrandonneurs.org/'

# One national RUSA event search returns every region's future events; we filter
# to the regions the team rides. This is deliberately NOT a per-region-number
# scrape: RUSA's region NUMBERS are opaque (San Francisco — the RBA for North Bay
# brevets like the Boonville Lollipop — doesn't surface under the obvious region
# numbers), whereas the Region LABEL column is stable and self-describing.
RUSA_NATIONAL_URL = 'https://rusa.org/cgi-bin/eventsearch_PF.pl?sortby=date'

# RUSA region label (as shown in the search's Region column) -> club.region used
# for the club_id lookup in upsert_event. One national fetch covers all of these.
TEAM_RUSA_REGIONS = {
    'CA: San Francisco': 'San Francisco',
    'CA: Davis': 'Davis',
    'CA: Santa Rosa': 'Santa Rosa',
    'CA: Santa Cruz': 'Santa Cruz',
    'CA: San Luis Obispo': 'San Luis Obispo',
}

# Santa Rosa Randonneurs brevet calendar
SRR_EVENTS_URL = 'https://www.santarosarandos.org/2026-brevets'

# San Luis Obispo Randonneurs brevet calendar
SLO_EVENTS_URL = 'https://slorandonneur.org/2026-brevets/'


def download_sfr_events():
    """
    Download and parse SFR events from Google Spreadsheet.
    
    NOTE: The Google Sheet must be publicly accessible for this to work.
    
    To make the sheet public:
    1. Open the Google Sheet
    2. Click "Share" button
    3. Change "Restricted" to "Anyone with the link"
    4. Set permission to "Viewer"
    """
    print("📥 Downloading SFR spreadsheet...")
    
    try:
        # Try to download the CSV
        response = urlopen(SFR_SHEET_URL, timeout=10)
        csv_data = response.read().decode('utf-8')
        
        # Check if we got HTML instead of CSV (redirect/auth page)
        if csv_data.strip().startswith('<'):
            print("❌ Sheet appears to be private or requires authentication")
            print("   Make the sheet public and try again.")
            return []
        
        # Parse CSV
        reader = csv.reader(StringIO(csv_data))
        rows = list(reader)
        
        if len(rows) < 2:
            print("❌ No data rows found in CSV")
            return []
        
        # Find header row and column indices
        header = rows[1]  # Row 2 is the header
        events = []
        
        # Map column names to indices
        col_map = {}
        for i, col_name in enumerate(header):
            col_map[col_name] = i
        
        # Parse data rows (starting from row 3)
        for row in rows[2:]:
            if len(row) < 5:  # Skip empty rows
                continue
            
            try:
                event_date = row[col_map.get('Event date', 0)].strip()
                event_name = row[col_map.get('Event', 1)].strip()
                start_time = row[col_map.get('Start time', 2)].strip()
                time_limit = row[col_map.get('Time limit', 3)].strip()
                rwgps_url = row[col_map.get('RideWithGPS link', 4)].strip()
                distance_miles = row[col_map.get('Length (mi)', 6)].strip()
                elevation_ft = row[col_map.get('Elev. gain (ft)', 7)].strip()
                start_location = row[col_map.get('Start/finish location', 9)].strip()
                
                # Skip if no date or name
                if not event_date or not event_name or event_date.startswith('20'):
                    continue
                
                # Parse distance in km from event name (e.g., "200k" -> 200)
                distance_km = 0
                parts = event_name.split()
                for part in parts:
                    if 'k' in part.lower() and part.lower() != 'k':
                        try:
                            distance_km = int(part.lower().replace('k', ''))
                            break
                        except ValueError:
                            pass
                
                # Parse time limit (e.g., "13.5 hrs" -> 13.5)
                time_limit_hours = None
                if time_limit and 'hrs' in time_limit.lower():
                    try:
                        time_limit_hours = float(time_limit.lower().replace('hrs', '').strip())
                    except ValueError:
                        pass
                
                # Skip events with invalid data
                if not distance_km:
                    continue
                
                # Parse elevation from CSV
                csv_elevation = None
                if elevation_ft:
                    try:
                        csv_elevation = int(elevation_ft.replace(',', '').replace("'", ''))
                    except (ValueError, AttributeError):
                        pass
                
                event = {
                    'date': event_date,
                    'name': event_name,
                    'distance_km': distance_km,
                    'distance_miles': None,  # Distance always from source table
                    'elevation_ft': csv_elevation,
                    'rwgps_url': rwgps_url if rwgps_url and rwgps_url not in ['n/a', 'coming soon', 'TBD'] else None,
                    'start_time': start_time if start_time and start_time != 'TBD' else None,
                    'time_limit_hours': time_limit_hours,
                    'start_location': start_location if start_location else None
                }
                events.append(event)
            except (IndexError, ValueError) as e:
                continue  # Skip malformed rows
        
        if events:
            print(f"✅ Downloaded {len(events)} SFR events from spreadsheet")
            
            # Fetch missing elevation from RideWithGPS (not distance)
            print("  Fetching missing elevation from RideWithGPS...")
            for event in events:
                if event['rwgps_url'] and ('ridewithgps.com' in event['rwgps_url']):
                    # Only fetch elevation if missing
                    if event['elevation_ft'] is None:
                        _, rwgps_elevation = get_rwgps_details(event['rwgps_url'])
                        if rwgps_elevation:
                            event['elevation_ft'] = rwgps_elevation
            
            return events
        else:
            print("⚠️  No valid events found in spreadsheet")
            return []
        
    except Exception as e:
        print(f"❌ Error downloading SFR events: {e}")
        return []


def get_rusa_events(fetch_rwgps=True):
    """Team-scoped RUSA calendar: the shared national scrape, filtered to the
    team's regions.

    Delegates all parsing to shared.rusa_calendar.get_rusa_events and passes
    TEAM_RUSA_REGIONS as the region filter, so every returned event carries
    event['region'] = the club.region string upsert_event looks up — exactly the
    behavior this function had before the parser moved to the shared library.
    """
    return _get_rusa_events(fetch_rwgps=fetch_rwgps, region_filter=TEAM_RUSA_REGIONS)


def get_davis_events():
    """Backward-compatible wrapper: the Davis subset of get_rusa_events()."""
    return [e for e in get_rusa_events() if e.get('region') == 'Davis']


def get_scr_events():
    """
    Download and parse Santa Cruz Randonneurs events from their website.
    Fetches additional details (distance, elevation) from RideWithGPS links.
    """
    print("📥 Downloading Santa Cruz Randonneurs events...")
    
    try:
        # Fetch SCR website
        req = Request(SCR_EVENTS_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html_content = response.read().decode('utf-8')
        
        events = []
        
        # Parse the events table - look for rows in the 2026 Events table
        # The rows use <th> tags, not <td>
        # Pattern: <th><strong>Date</strong></th><th><a href="URL"><strong>Route</strong></a></th>...
        table_pattern = r'<th[^>]*><strong>(.*?)</strong></th>\s*<th[^>]*><a[^>]*href="([^"]*)"[^>]*><strong>(.*?)</strong></a></th>\s*<th[^>]*><strong>(.*?)</strong></th>\s*<th[^>]*><strong>(.*?)</strong></th>'
        
        matches = re.findall(table_pattern, html_content, re.DOTALL)
        
        for match in matches:
            date_str, route_url, route_name, location, start_time = match
            
            # Clean up the data
            date_str = date_str.strip()
            route_url = route_url.strip()
            route_name = html.unescape(route_name.strip())
            location = location.strip()
            start_time = start_time.strip()
            
            # Skip if not a valid date or if it's a range (like "Monday, Nov 9 - Friday, Nov 13")
            if '-' in date_str or 'TBD' in start_time or not date_str:
                continue
            
            # Parse date - format is like "Sunday, March 1" or "Saturday, March 7"
            # We need to add the year (2026)
            date_match = re.search(r'(\w+),\s*(\w+)\s+(\d+)', date_str)
            if not date_match:
                continue
            
            month_name = date_match.group(2)
            day = date_match.group(3)
            
            # Convert month name to number
            months = {
                'January': '01', 'February': '02', 'March': '03', 'April': '04',
                'May': '05', 'June': '06', 'July': '07', 'August': '08',
                'September': '09', 'October': '10', 'November': '11', 'December': '12'
            }
            month_num = months.get(month_name)
            if not month_num:
                continue
            
            event_date = f"2026-{month_num}-{day.zfill(2)}"
            
            # Extract distance from route name (e.g., "200k" -> 200)
            distance_match = re.search(r'(\d+)k', route_name, re.IGNORECASE)
            distance_km = int(distance_match.group(1)) if distance_match else 0
            
            # Skip if no distance found
            if not distance_km:
                continue
            
            # Extract RWGPS URL if it's a ridewithgps link
            rwgps_url = None
            elevation_ft = None
            
            if 'ridewithgps.com' in route_url:
                rwgps_url = route_url
                # Fetch elevation from RWGPS (not distance)
                print(f"  Fetching elevation for {route_name}...")
                _, elevation_ft = get_rwgps_details(rwgps_url)
            
            # Determine start location based on the Location column
            if 'Santa Cruz' in location:
                start_location = 'Santa Cruz Lighthouse'
            elif 'Carmel' in location:
                start_location = 'Carmel'
            else:
                start_location = location
            
            event = {
                'date': event_date,
                'name': route_name,
                'distance_km': distance_km,
                'distance_miles': None,  # Distance always from source table
                'elevation_ft': elevation_ft,
                'rwgps_url': rwgps_url,
                'start_time': start_time,
                'time_limit_hours': get_time_limit_hours(distance_km),
                'start_location': start_location
            }
            events.append(event)
        
        if events:
            print(f"✅ Downloaded {len(events)} SCR events")
        else:
            print("⚠️  No SCR events found")
        
        return events
        
    except Exception as e:
        print(f"❌ Error downloading SCR events: {e}")
        return []


def get_srr_events():
    """
    Download and parse Santa Rosa Randonneurs events from their website.
    The page is a Google Docs embed with heavily nested spans.
    Fetches elevation from RideWithGPS links when available.
    """
    print("📥 Downloading Santa Rosa Randonneurs events...")

    try:
        req = Request(SRR_EVENTS_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html_content = response.read().decode('utf-8')

        events = []

        months = {
            'january': '01', 'february': '02', 'march': '03',
            'april': '04', 'may': '05', 'june': '06',
            'july': '07', 'august': '08', 'september': '09',
            'october': '10', 'november': '11', 'december': '12',
        }

        # The page embeds a Google Doc. Dates are in <h3> tags with nested
        # spans (digits may be split across spans). Event names and RWGPS
        # links are in <h2> tags. Start times appear in paragraphs after
        # each event heading.

        # Step 1: collect ALL h3 headers with document positions.
        # Valid date headers get (pos, day, month_name); invalid ones get
        # (pos, None, None) — used to detect "no specific date" events.
        h3_all = []
        h3_dates = []
        for m in re.finditer(
            r'<h3[^>]*>(.*?)</h3>', html_content, re.DOTALL | re.IGNORECASE
        ):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            date_match = re.search(
                r'(\d+)\s+(\w+)\s+\((\w+)\)', text, re.IGNORECASE
            )
            if date_match:
                entry = (m.start(), date_match.group(1), date_match.group(2))
                h3_all.append(entry)
                h3_dates.append(entry)
            elif text:  # non-empty h3 with no parseable date
                h3_all.append((m.start(), None, None))

        # Step 2: collect RWGPS links from <h2> tags with their positions.
        # Inner span text is concatenated by stripping inner HTML tags.
        rwgps_events = []
        for m in re.finditer(
            r'<h2[^>]*>.*?href="(https://ridewithgps\.com/routes/\d+)"'
            r'[^>]*>(.*?)</a>.*?</h2>',
            html_content, re.DOTALL | re.IGNORECASE
        ):
            url = m.group(1)
            name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if name:
                rwgps_events.append((m.start(), url, name))

        # Step 3: collect start times with positions.
        start_times = [
            (m.start(), m.group(1))
            for m in re.finditer(r'start:\s*(\d{4})', html_content, re.IGNORECASE)
        ]

        # Step 4: associate each event with its nearest preceding date header
        # and nearest following start time (before the next RWGPS event).
        for i, (rwgps_pos, url, name) in enumerate(rwgps_events):
            # Find the nearest preceding h3 (valid or not)
            preceding_all = [h for h in h3_all if h[0] < rwgps_pos]
            if not preceding_all:
                continue
            nearest_h3 = max(preceding_all, key=lambda x: x[0])
            # If the nearest h3 has no parseable date, skip this event
            if nearest_h3[1] is None:
                continue
            _, day, month_name = nearest_h3

            month_num = months.get(month_name.lower())
            if not month_num:
                continue  # e.g. "april (date upon request)" — no specific date

            event_date = f"2026-{month_num}-{day.zfill(2)}"

            # Distance from name (e.g. "West County 200km" → 200)
            dist_match = re.search(r'(\d+)km', name, re.IGNORECASE)
            distance_km = int(dist_match.group(1)) if dist_match else 0
            if not distance_km:
                continue

            # Start time between this and the next RWGPS event
            next_pos = rwgps_events[i + 1][0] if i + 1 < len(rwgps_events) else len(html_content)
            window_starts = [(p, t) for p, t in start_times if rwgps_pos < p < next_pos]
            start_time = None
            if window_starts:
                raw = min(window_starts, key=lambda x: x[0])[1]
                start_time = f"{raw[:2]}:{raw[2:]}" if len(raw) == 4 else None

            # Fetch elevation from RideWithGPS
            elevation_ft = None
            if url:
                print(f"  Fetching elevation for {name}...")
                _, elevation_ft = get_rwgps_details(url)

            events.append({
                'date': event_date,
                'name': name,
                'distance_km': distance_km,
                'distance_miles': None,
                'elevation_ft': elevation_ft,
                'rwgps_url': url,
                'start_time': start_time,
                'time_limit_hours': get_time_limit_hours(distance_km),
                'start_location': 'Santa Rosa, CA',
            })

        if events:
            print(f"✅ Downloaded {len(events)} SRR events")
        else:
            print("⚠️  No SRR events found")

        return events

    except Exception as e:
        print(f"❌ Error downloading SRR events: {e}")
        return []


def get_slo_events():
    """
    Download and parse San Luis Obispo Randonneurs events from their website.
    The page is a WordPress post with date text inline before each event link.
    Populaires (sub-200km) are returned but filtered by upsert_event().
    """
    print("📥 Downloading San Luis Obispo Randonneurs events...")

    try:
        req = Request(SLO_EVENTS_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html_content = response.read().decode('utf-8')

        events = []

        months = {
            'jan': '01', 'feb': '02', 'mar': '03',
            'apr': '04', 'may': '05', 'jun': '06',
            'jul': '07', 'aug': '08', 'sep': '09',
            'oct': '10', 'nov': '11', 'dec': '12',
        }

        # Narrow to the entry-content section to avoid sidebar noise.
        start = html_content.find('entry-content')
        content = html_content[start:] if start >= 0 else html_content

        # Each event line looks like:
        #   Jan 3   <a href="...">200k Morro Bay More Coastal</a> (ACP)
        #   March 28   <a href="...">300k Mostly SLO (ACP)</a>
        # Some events (e.g. 400k) have HTML tags between the date text and
        # the link, and nested tags inside the link. Allow for both.
        pattern = re.compile(
            r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May'
            r'|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?'
            r'|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
            r'\s+(\d+)(?:[^<]|<[^>]+>)*?'
            r'<a[^>]+href="[^"]*"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )

        seen = set()  # deduplicate (date, name) pairs
        for m in pattern.finditer(content):
            month_word = m.group(1)[:3].lower()
            day = m.group(2)
            link_text = re.sub(r'<[^>]+>', '', m.group(3)).strip()
            link_text = html.unescape(link_text)
            dist_match = re.search(r'^(\d+)k\s*(.*)', link_text, re.IGNORECASE)
            if not dist_match:
                continue
            distance_km = int(dist_match.group(1))
            route_name = dist_match.group(2).strip()
            full_name = f"{dist_match.group(1)}k {route_name}"

            month_num = months.get(month_word)
            if not month_num:
                continue

            event_date = f"2026-{month_num}-{day.zfill(2)}"
            key = (event_date, full_name)
            if key in seen:
                continue
            seen.add(key)

            # Infer start location from event name
            if 'morro bay' in full_name.lower():
                start_location = 'Morro Bay, CA'
            else:
                start_location = 'San Luis Obispo, CA'

            events.append({
                'date': event_date,
                'name': full_name,
                'distance_km': distance_km,
                'distance_miles': None,
                'elevation_ft': None,
                'rwgps_url': None,
                'start_time': None,
                'time_limit_hours': get_time_limit_hours(distance_km),
                'start_location': start_location,
            })

        if events:
            print(f"✅ Downloaded {len(events)} SLO events")
        else:
            print("⚠️  No SLO events found")

        return events

    except Exception as e:
        print(f"❌ Error downloading SLO events: {e}")
        return []


def upsert_event(cursor, region, event):
    """Insert or update a RUSA event in the ride table. Only processes rides with valid ACP time limits."""
    # Filter: only process rides that have standard ACP time limits
    if get_time_limit_hours(event['distance_km']) is None:
        return 'filtered'
    
    # Get club_id for this region
    cursor.execute("""
        SELECT id FROM club WHERE region = %s LIMIT 1
    """, (region,))
    club_result = cursor.fetchone()
    if not club_result:
        print(f"❌ No club found for region {region}")
        return 'error'
    club_id = club_result[0]
    
    # Get current season
    cursor.execute("""
        SELECT id FROM season WHERE is_current = TRUE LIMIT 1
    """)
    season_result = cursor.fetchone()
    if not season_result:
        print(f"❌ No current season found")
        return 'error'
    season_id = season_result[0]
    
    # Check if this event already exists (external events only). Match either by
    # exact (date, name) — the historical key — or by (date, club, distance), so
    # the same ride coming from two sources with different names (e.g. RUSA's
    # 'HBUH 200' vs the club site's 'Healdsburg-Boonville-Ukiah 200km') updates
    # one row instead of creating a duplicate. Exact-name matches win the tie.
    cursor.execute("""
        SELECT ri.id, ri.event_status
        FROM ride ri
        INNER JOIN club c ON ri.club_id = c.id
        WHERE c.code != 'TA'
          AND ri.date = %s
          AND (ri.name = %s OR (ri.club_id = %s AND ri.distance_km = %s))
        ORDER BY (ri.name = %s) DESC
        LIMIT 1
    """, (event['date'], event['name'], club_id, event['distance_km'], event['name']))

    existing = cursor.fetchone()
    
    # Default to ACP brevet if not specified
    ride_type = event.get('ride_type', 'ACP brevet')
    
    # Calculate ft_per_mile
    ft_per_mile = None
    if event.get('elevation_ft') and event.get('distance_miles') and event['distance_miles'] > 0:
        ft_per_mile = event['elevation_ft'] / event['distance_miles']
    
    if existing:
        # Skip updating if event status is COMPLETED
        if existing[1] == 'COMPLETED':
            return 'skipped'
        
        # Update existing event (don't modify event_status or name). COALESCE the
        # soft/enrichment fields so a sparse source (e.g. a RUSA row with no route
        # assigned yet → no RWGPS/elevation/start) never wipes richer data a club
        # site already provided for the same ride.
        cursor.execute("""
            UPDATE ride
            SET club_id = %s,
                ride_type = %s,
                distance_km = %s,
                distance_miles = COALESCE(%s, distance_miles),
                elevation_ft = COALESCE(%s, elevation_ft),
                ft_per_mile = COALESCE(%s, ft_per_mile),
                rwgps_url = COALESCE(%s, rwgps_url),
                start_time = COALESCE(%s, start_time),
                time_limit_hours = %s,
                start_location = COALESCE(%s, start_location)
            WHERE id = %s
        """, (
            club_id,
            ride_type,
            event['distance_km'],
            event['distance_miles'],
            event['elevation_ft'],
            ft_per_mile,
            event['rwgps_url'],
            event['start_time'],
            event['time_limit_hours'],
            event['start_location'],
            existing[0]
        ))
        return 'updated'
    else:
        # Insert new event with UPCOMING status
        cursor.execute("""
            INSERT INTO ride 
            (name, ride_type, date, distance_km, distance_miles, 
             elevation_ft, ft_per_mile, rwgps_url, start_time, 
             time_limit_hours, start_location, event_status,
             club_id, season_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            event['name'],
            ride_type,
            event['date'],
            event['distance_km'],
            event['distance_miles'],
            event['elevation_ft'],
            ft_per_mile,
            event['rwgps_url'],
            event['start_time'],
            event['time_limit_hours'],
            event['start_location'],
            'UPCOMING',
            club_id,
            season_id
        ))
        return 'inserted'


def main():
    """Update all RUSA events in the database."""
    print("=" * 60)
    print("Updating RUSA Calendar Events")
    print("=" * 60)

    if not DATABASE_URL:
        print("❌ DATABASE_URL environment variable not set")
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Counter so any action key (including 'error') is safe to increment — a
    # missing club/season row returns 'error' and must not crash the daily run.
    stats = Counter()
    
    # Download and process SFR events
    print("\n📍 San Francisco Randonneurs")
    sfr_events = download_sfr_events()
    for event in sfr_events:
        action = upsert_event(cursor, 'San Francisco', event)
        stats[action] += 1
        if action == 'skipped':
            print(f"  ⊘ {event['name']} ({event['date']}) [DONE - skipped]")
        elif action == 'filtered':
            print(f"  ⊗ {event['name']} ({event['date']}) [{event['distance_km']}km - filtered]")
        else:
            print(f"  {'✓' if action == 'updated' else '+'} {event['name']} ({event['date']})")
    
    # Process SCR events (when available)
    scr_events = get_scr_events()
    if scr_events:
        print("\n📍 Santa Cruz Randonneurs")
        for event in scr_events:
            action = upsert_event(cursor, 'Santa Cruz', event)
            stats[action] += 1
            if action == 'skipped':
                print(f"  ⊘ {event['name']} ({event['date']}) [DONE - skipped]")
            elif action == 'filtered':
                print(f"  ⊗ {event['name']} ({event['date']}) [{event['distance_km']}km - filtered]")
            else:
                print(f"  {'✓' if action == 'updated' else '+'} {event['name']} ({event['date']})")

    # Process Santa Rosa Randonneurs events
    srr_events = get_srr_events()
    if srr_events:
        print("\n📍 Santa Rosa Randonneurs")
        for event in srr_events:
            action = upsert_event(cursor, 'Santa Rosa', event)
            stats[action] += 1
            if action == 'skipped':
                print(f"  ⊘ {event['name']} ({event['date']}) [DONE - skipped]")
            elif action == 'filtered':
                print(f"  ⊗ {event['name']} ({event['date']}) [{event['distance_km']}km - filtered]")
            else:
                print(f"  {'✓' if action == 'updated' else '+'} {event['name']} ({event['date']})")

    # Process San Luis Obispo Randonneurs events
    slo_events = get_slo_events()
    if slo_events:
        print("\n📍 San Luis Obispo Randonneurs")
        for event in slo_events:
            action = upsert_event(cursor, 'San Luis Obispo', event)
            stats[action] += 1
            if action == 'skipped':
                print(f"  ⊘ {event['name']} ({event['date']}) [DONE - skipped]")
            elif action == 'filtered':
                print(f"  ⊗ {event['name']} ({event['date']}) [{event['distance_km']}km - filtered]")
            else:
                print(f"  {'✓' if action == 'updated' else '+'} {event['name']} ({event['date']})")

    # Process the RUSA calendar last: one national fetch covering every team
    # region (SF, Davis, Santa Rosa, Santa Cruz, SLO). Running after the club
    # sites means a club's friendlier event name wins on shared rides, while RUSA
    # fills in brevets the club sites don't list yet (e.g. SF's Boonville Lollipop,
    # which is on RUSA but missing from the SFR Google Sheet).
    print("\n📍 RUSA calendar (all team regions)")
    rusa_events = get_rusa_events()
    for event in rusa_events:
        action = upsert_event(cursor, event['region'], event)
        stats[action] += 1
        tag = event.get('region', '')
        if action == 'skipped':
            print(f"  ⊘ {event['name']} ({event['date']}) [{tag} - DONE]")
        elif action == 'filtered':
            print(f"  ⊗ {event['name']} ({event['date']}) [{event['distance_km']}km - filtered]")
        else:
            print(f"  {'✓' if action == 'updated' else '+'} {event['name']} ({event['date']}) [{tag}]")

    conn.commit()
    conn.close()
    
    print("\n" + "=" * 60)
    print(f"✅ Done! {stats['inserted']} inserted, {stats['updated']} updated, "
          f"{stats['skipped']} skipped (DONE), {stats['filtered']} filtered (distance), "
          f"{stats['error']} errors")
    print("=" * 60)


if __name__ == '__main__':
    main()
