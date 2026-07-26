"""RUSA national-calendar scraper — the club-agnostic parsing engine.

This is the genuinely reusable core of Team Asha's RUSA collector, extracted
verbatim so both the Team Asha app (`scripts/update_rusa_events.py`, now a thin
importer of this module) and BrevetHub can parse RUSA's national event listing
from the same source of truth. It is deliberately **standalone**: it imports only
the stdlib, never Flask (no application-context globals), and touches no database
— callers own persistence. `tests/brevethub/test_shared_isolation.py` enforces
that boundary.

What it parses, and — just as importantly — what it does NOT invent:
  * ``get_rusa_events`` reads RUSA's printer-friendly national search
    (``eventsearch_PF.pl``) and returns one dict per ACP/RUSA brevet. The national
    feed carries **no per-event start time or start location**, so every returned
    event has ``start_time = None`` and ``start_location = None``. This module
    never fabricates those values (a route-detail page yields only a RideWithGPS
    href, not a start location — see ``get_rwgps_url_from_route``).
  * ``region_filter`` parametrizes which regions to keep. ``None`` (the default)
    keeps every ACP/RUSA brevet and tags it with RUSA's raw region label
    (e.g. ``"CA: San Francisco"``). A dict — label -> caller's region string —
    keeps only its keys and tags each event with the mapped value (this is how the
    Team Asha shim reproduces its old ``TEAM_RUSA_REGIONS`` behavior byte-for-byte).

``urlopen``/``Request`` are imported at module level so tests patch
``shared.rusa_calendar.urlopen``.
"""
import re
import html
from urllib.request import urlopen, Request


# One national RUSA event search returns every region's future events; callers
# filter with ``region_filter``. This is deliberately NOT a per-region-number
# scrape: RUSA's region NUMBERS are opaque (San Francisco — the RBA for North Bay
# brevets like the Boonville Lollipop — doesn't surface under the obvious region
# numbers), whereas the Region LABEL column is stable and self-describing.
RUSA_NATIONAL_URL = 'https://rusa.org/cgi-bin/eventsearch_PF.pl?sortby=date'
BREVET_EVENT_TYPES = frozenset({'ACP brevet', 'RUSA brevet'})
SANCTIONED_EVENT_TYPES = frozenset({
    'ACP Trace',
    'ACP brevet',
    'ACP flèche',
    'RM randonnée',
    'RUSA arrow/dart/dart populaire',
    'RUSA brevet',
    'RUSA populaire',
    'UAF brevet',
})


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


def get_rwgps_url_from_route(route_id):
    """Fetch RWGPS URL from RUSA route detail page.

    A RUSA route-detail page (``routeview_PF.pl?rtid=``) exposes only the route's
    RideWithGPS href — NOT a start location or time. This returns that href (or
    None); it never derives a start location from the page.
    """
    try:
        route_url = f'https://rusa.org/cgi-bin/routeview_PF.pl?rtid={route_id}'
        req = Request(route_url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html_content = response.read().decode('utf-8')

        # Look for ridewithgps.com links
        rwgps_match = re.search(r'href=["\']?(https://ridewithgps\.com/routes/\d+)["\']?', html_content, re.IGNORECASE)
        if rwgps_match:
            return rwgps_match.group(1)

        return None
    except Exception as e:
        return None


def get_rwgps_details(rwgps_url):
    """Fetch distance and elevation from a RideWithGPS URL."""
    try:
        # Fetch the route page
        req = Request(rwgps_url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=10)
        html_content = response.read().decode('utf-8')

        # Parse distance and elevation from Open Graph meta tag
        # Format: "125.4 mi, +7490 ft. Bike ride in..."
        # The meta tag can have attributes in any order
        og_desc_match = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]*>', html_content)

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


def get_rusa_events(fetch_rwgps=True, region_filter=None,
                    include_all_sanctioned=False):
    """Download RUSA's national calendar and return sanctioned events.

    By default, preserves Team Asha's existing ACP/RUSA brevet-only behavior.
    ``include_all_sanctioned=True`` enables BrevetHub's national directory mode:
    ACP brevets/flèches/traces, RM randonnées, RUSA brevets/populaires/team events,
    and UAF brevets. ``region_filter`` controls region scoping:
      * ``None`` — keep every region; ``event['region']`` is RUSA's raw label.
      * a dict (label -> region string) — keep only its labels; ``event['region']``
        is the mapped value (how the Team Asha shim tags each event with the
        ``club.region`` string its upsert uses).
    When ``fetch_rwgps`` is True, follows each route's detail page for a
    RideWithGPS link and elevation. Every event has ``start_time``/``start_location``
    of ``None`` — the national feed does not carry them and this never fabricates
    them. Returns [] on any network/parse error so a RUSA hiccup never breaks a batch.
    """
    print("📥 Downloading RUSA event calendar from rusa.org...")

    try:
        req = Request(RUSA_NATIONAL_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req, timeout=30)
        html_content = response.read().decode('utf-8', 'replace')

        events = []

        # Parse the events table
        # Columns: Region | Type | Date | Distance | Climbing | Route | Website

        # Find all table rows (case insensitive)
        row_pattern = r'<TR[^>]*>(.*?)</TR>'
        rows = re.findall(row_pattern, html_content, re.DOTALL | re.IGNORECASE)

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

            # Region scoping: when a mapping is supplied, keep only its regions;
            # when None, keep every region.
            if region_filter is not None and region not in region_filter:
                continue

            # Extract event type (first line before any divs)
            event_type = re.split(r'<div', event_type_raw)[0]
            event_type = html.unescape(
                re.sub(r'<[^>]+>', '', event_type).strip())

            accepted_types = (
                SANCTIONED_EVENT_TYPES
                if include_all_sanctioned
                else BREVET_EVENT_TYPES
            )
            if event_type not in accepted_types:
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
                route_name = html.unescape(route_name_match.group(1).strip())
            else:
                route_name = html.unescape(re.sub(r'<[^>]+>', '', route_cell).strip())

            # Extract route ID to fetch RWGPS URL
            route_id_match = re.search(r'rtid=(\d+)', route_cell, re.IGNORECASE)
            route_id = route_id_match.group(1) if route_id_match else None

            # Try to get RWGPS URL from route detail page
            rwgps_url = None
            elevation_ft = rusa_elevation_ft  # Start with RUSA elevation

            if route_id and fetch_rwgps:
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
                'start_time': None,       # national feed carries no start time
                'time_limit_hours': get_time_limit_hours(distance_km),
                'start_location': None,   # national feed carries no start location
                'ride_type': event_type,
                'route_id': route_id,
                # Mapped region string when a filter is supplied, else the raw label.
                'region': region_filter[region] if region_filter is not None else region,
            }
            events.append(event)

        if events:
            print(f"✅ Downloaded {len(events)} RUSA calendar events")
        else:
            print("⚠️  No matching RUSA calendar events found")

        return events

    except Exception as e:
        print(f"❌ Error downloading RUSA events: {e}")
        import traceback
        traceback.print_exc()
        return []
