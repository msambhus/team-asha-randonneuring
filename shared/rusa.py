"""RUSA results scraper — fetches official finish times from rusa.org."""
import requests
from bs4 import BeautifulSoup
from datetime import datetime


def fetch_rider_results(rusa_id):
    """Scrape RUSA results page for a rider and return parsed results.

    Args:
        rusa_id: RUSA member ID (integer or string)

    Returns:
        list of dicts: [{'date': datetime.date, 'distance_km': int,
                         'finish_time': str, 'route_name': str}, ...]
        Empty list on error. ``route_name`` is the event/route label from the
        results table (additive key; Team Asha's finish-time matcher ignores it).
    """
    try:
        url = f"https://rusa.org/cgi-bin/resultsearch_PF.pl?mid={rusa_id}&sortby=date"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        table = soup.find('table')
        if not table:
            return []

        results = []
        rows = table.find_all('tr')

        for row in rows:
            cells = row.find_all('td')
            # Data rows have exactly 8 cells:
            # Cert No., Type, Km, Climbing, Date, Region, Route, Time
            if len(cells) != 8:
                continue

            text = [c.get_text(strip=True) for c in cells]
            cert_no, _type, km_str, _climbing, date_str, _region, route_name, time_str = text

            # Skip year-summary rows (cert_no is a 4-digit year)
            if len(cert_no) == 4 and cert_no.isdigit():
                continue

            # Skip rows with missing data
            if not date_str or not km_str or not time_str:
                continue

            try:
                ride_date = datetime.strptime(date_str, '%Y/%m/%d').date()
                distance_km = int(km_str.replace(',', ''))
                results.append({
                    'date': ride_date,
                    'distance_km': distance_km,
                    'finish_time': time_str,
                    'route_name': route_name,
                })
            except (ValueError, TypeError):
                continue

        return results

    except requests.RequestException:
        return []
    except Exception:
        return []
