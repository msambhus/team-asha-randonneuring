#!/usr/bin/env python3
"""
Script to update upcoming_rusa_event table with data from SFR Google Spreadsheet.
Run this script to refresh the RUSA calendar events.

Usage:
    python scripts/update_rusa_events.py
"""

import sqlite3
import sys
import csv
import re
from pathlib import Path
from urllib.request import urlopen, Request
from io import StringIO
from html.parser import HTMLParser

# Add parent directory to path to import from project
sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent.parent / 'data' / 'team_asha.db'

# Google Sheets URL - convert to CSV export URL
SFR_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1LO6FfMJeMP_cvnEUtCfBvpmVudLNzWH-dRVv_PWqLqQ/export?format=csv&gid=0'


def get_time_limit_hours(distance_km):
    """Calculate standard RUSA/ACP time limit in hours based on distance."""
    if distance_km == 200:
        return 13.5
    elif distance_km == 300:
        return 20
    elif distance_km == 400:
        return 27
    elif distance_km == 600:
        return 40
    elif distance_km == 1000:
        return 75
    return None

# Santa Cruz Randonneurs website
SCR_EVENTS_URL = 'https://santacruzrandonneurs.org/'

# RUSA website for region 4 (includes Davis)
RUSA_REGION4_URL = 'https://rusa.org/cgi-bin/eventsearch_PF.pl?region=4&sortby=date'


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


def get_rwgps_url_from_route(route_id):
    """Fetch RWGPS URL from RUSA route detail page."""
    try:
        route_url = f'https://rusa.org/cgi-bin/routeview_PF.pl?rtid={route_id}'
        req = Request(route_url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html = response.read().decode('utf-8')
        
        # Look for ridewithgps.com links
        rwgps_match = re.search(r'href=["\']?(https://ridewithgps\.com/routes/\d+)["\']?', html, re.IGNORECASE)
        if rwgps_match:
            return rwgps_match.group(1)
        
        return None
    except Exception as e:
        return None


def get_davis_events():
    """
    Download and parse Davis (Gold Country Randonneurs) events from RUSA website.
    Filters for ACP brevet and RUSA brevet events only.
    Fetches RWGPS links from route detail pages and prioritizes RWGPS data.
    """
    print("📥 Downloading Davis (Gold Country Randonneurs) events from RUSA...")
    
    try:
        # Fetch RUSA region 4 page
        req = Request(RUSA_REGION4_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html = response.read().decode('utf-8')
        
        events = []
        
        # Parse the events table
        # Columns: Region | Type | Date | Distance | Climbing | Route | Website
        
        # Find all table rows (case insensitive)
        row_pattern = r'<TR[^>]*>(.*?)</TR>'
        rows = re.findall(row_pattern, html, re.DOTALL | re.IGNORECASE)
        
        for row_html in rows:
            # Extract all cells from this row (case insensitive)
            cell_pattern = r'<TD[^>]*>(.*?)</TD>'
            cells = re.findall(cell_pattern, row_html, re.DOTALL | re.IGNORECASE)
            
            if len(cells) < 7:
                continue
            
            # Parse cells
            region = re.sub(r'<[^>]+>', '', cells[0]).strip()
            event_type_raw = cells[1]
            date_str = re.sub(r'<[^>]+>', '', cells[2]).strip()
            distance_raw = cells[3]
            climbing_raw = cells[4]
            route_cell = cells[5]
            
            # Filter for Davis region only
            if region != 'CA: Davis':
                continue
            
            # Extract event type (first line before any divs)
            event_type = re.split(r'<div', event_type_raw)[0]
            event_type = re.sub(r'<[^>]+>', '', event_type).strip()
            
            # Filter for ACP brevet or RUSA brevet only
            if event_type not in ['ACP brevet', 'RUSA brevet']:
                continue
            
            # Parse date (format: YYYY/MM/DD)
            date_match = re.search(r'(\d{4})/(\d{2})/(\d{2})', date_str)
            if not date_match:
                continue
            
            event_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
            
            # Parse distance - extract just the number before any div
            distance_text = re.split(r'<div', distance_raw)[0]
            distance_text = re.sub(r'<[^>]+>', '', distance_text).strip()
            distance_km = 0
            try:
                distance_km = int(distance_text)
            except ValueError:
                continue
            
            # Parse elevation from Climbing column (format: "4,489'" or just a number)
            climbing_text = re.sub(r'<[^>]+>', '', climbing_raw).strip()
            rusa_elevation_ft = None
            if climbing_text and climbing_text != '\xa0' and climbing_text:
                elev_match = re.search(r"([\d,]+)", climbing_text)
                if elev_match:
                    try:
                        rusa_elevation_ft = int(elev_match.group(1).replace(',', ''))
                    except ValueError:
                        pass
            
            # Extract route name from Route column
            route_name_match = re.search(r'<A[^>]*>([^<]+)</A>', route_cell, re.IGNORECASE)
            if route_name_match:
                route_name = route_name_match.group(1).strip()
            else:
                route_name = re.sub(r'<[^>]+>', '', route_cell).strip()
            
            # Extract route ID to fetch RWGPS URL
            route_id_match = re.search(r'rtid=(\d+)', route_cell, re.IGNORECASE)
            route_id = route_id_match.group(1) if route_id_match else None
            
            # Try to get RWGPS URL from route detail page
            rwgps_url = None
            elevation_ft = rusa_elevation_ft  # Start with RUSA elevation
            
            if route_id:
                print(f"  Checking route {route_id} for RWGPS link...")
                rwgps_url = get_rwgps_url_from_route(route_id)
                
                # If RWGPS URL found, fetch elevation from RWGPS (not distance)
                if rwgps_url:
                    print(f"  Found RWGPS link, fetching elevation...")
                    _, rwgps_elevation = get_rwgps_details(rwgps_url)
                    if rwgps_elevation:
                        elevation_ft = rwgps_elevation  # RWGPS elevation overrides RUSA data
            
            event = {
                'date': event_date,
                'name': route_name,
                'distance_km': distance_km,
                'distance_miles': None,  # Distance always from RUSA table
                'elevation_ft': elevation_ft,
                'rwgps_url': rwgps_url,
                'start_time': None,
                'time_limit_hours': get_time_limit_hours(distance_km),
                'start_location': None,
                'ride_type': event_type
            }
            events.append(event)
        
        if events:
            print(f"✅ Downloaded {len(events)} Davis events from RUSA")
        else:
            print("⚠️  No Davis ACP/RUSA brevet events found")
        
        return events
        
    except Exception as e:
        print(f"❌ Error downloading Davis events: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_rwgps_details(rwgps_url):
    """Fetch distance and elevation from a RideWithGPS URL."""
    try:
        # Fetch the route page
        req = Request(rwgps_url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html = response.read().decode('utf-8')
        
        # Parse distance and elevation from Open Graph meta tag
        # Format: "125.4 mi, +7490 ft. Bike ride in..."
        # The meta tag can have attributes in any order
        og_desc_match = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]*>', html)
        
        distance_miles = None
        elevation_ft = None
        description = None
        
        if og_desc_match:
            # Extract the content attribute value from the meta tag
            meta_tag = og_desc_match.group(0)
            content_match = re.search(r'content=["\']([^"\']+)', meta_tag)
            if content_match:
                description = content_match.group(1)
        
        if description:
            # Extract distance (e.g., "125.4 mi")
            distance_match = re.search(r'([\d.]+)\s*mi', description)
            if distance_match:
                distance_miles = float(distance_match.group(1))
            
            # Extract elevation (e.g., "+7490 ft" or "+7,490 ft")
            elevation_match = re.search(r'\+\s*([\d,]+)\s*ft', description)
            if elevation_match:
                elevation_str = elevation_match.group(1).replace(',', '')
                elevation_ft = int(elevation_str)
        
        return distance_miles, elevation_ft
        
    except Exception as e:
        print(f"  ⚠️  Could not fetch details from {rwgps_url}: {e}")
        return None, None


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
        html = response.read().decode('utf-8')
        
        events = []
        
        # Parse the events table - look for rows in the 2026 Events table
        # The rows use <th> tags, not <td>
        # Pattern: <th><strong>Date</strong></th><th><a href="URL"><strong>Route</strong></a></th>...
        table_pattern = r'<th[^>]*><strong>(.*?)</strong></th>\s*<th[^>]*><a[^>]*href="([^"]*)"[^>]*><strong>(.*?)</strong></a></th>\s*<th[^>]*><strong>(.*?)</strong></th>\s*<th[^>]*><strong>(.*?)</strong></th>'
        
        matches = re.findall(table_pattern, html, re.DOTALL)
        
        for match in matches:
            date_str, route_url, route_name, location, start_time = match
            
            # Clean up the data
            date_str = date_str.strip()
            route_url = route_url.strip()
            route_name = route_name.strip()
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


def upsert_event(cursor, region, event):
    """Insert or update a RUSA event. Only processes rides with valid ACP time limits."""
    # Filter: only process rides that have standard ACP time limits
    if get_time_limit_hours(event['distance_km']) is None:
        return 'filtered'
    
    # Check if event exists
    cursor.execute("""
        SELECT id, event_status FROM upcoming_rusa_event 
        WHERE date = ? AND route_name = ?
    """, (event['date'], event['name']))
    
    existing = cursor.fetchone()
    
    # Default to ACP brevet if not specified
    ride_type = event.get('ride_type', 'ACP brevet')
    
    if existing:
        # Skip updating if event status is DONE
        if existing[1] == 'DONE':
            return 'skipped'
        
        # Update existing event (don't modify event_status)
        cursor.execute("""
            UPDATE upcoming_rusa_event 
            SET region = ?,
                ride_type = ?,
                distance_km = ?,
                distance_miles = ?,
                elevation_ft = ?,
                rwgps_url = ?,
                start_time = ?,
                time_limit_hours = ?,
                start_location = ?
            WHERE id = ?
        """, (
            region,
            ride_type,
            event['distance_km'],
            event['distance_miles'],
            event['elevation_ft'],
            event['rwgps_url'],
            event['start_time'],
            event['time_limit_hours'],
            event['start_location'],
            existing[0]
        ))
        return 'updated'
    else:
        # Insert new event with ACTIVE status
        cursor.execute("""
            INSERT INTO upcoming_rusa_event 
            (region, ride_type, date, distance_km, route_name, 
             distance_miles, elevation_ft, rwgps_url, start_time, 
             time_limit_hours, start_location, event_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            region,
            ride_type,
            event['date'],
            event['distance_km'],
            event['name'],
            event['distance_miles'],
            event['elevation_ft'],
            event['rwgps_url'],
            event['start_time'],
            event['time_limit_hours'],
            event['start_location'],
            'ACTIVE'
        ))
        return 'inserted'


def main():
    """Update all RUSA events in the database."""
    print("=" * 60)
    print("Updating RUSA Calendar Events")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    stats = {'inserted': 0, 'updated': 0, 'skipped': 0, 'filtered': 0}
    
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
    
    # Process Davis events
    print("\n📍 Davis Bike Club")
    davis_events = get_davis_events()
    for event in davis_events:
        action = upsert_event(cursor, 'Davis', event)
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
    
    conn.commit()
    conn.close()
    
    print("\n" + "=" * 60)
    print(f"✅ Done! {stats['inserted']} inserted, {stats['updated']} updated, {stats['skipped']} skipped (DONE), {stats['filtered']} filtered (distance)")
    print("=" * 60)


if __name__ == '__main__':
    main()
