"""RUSA ID validator - scrapes RUSA website to validate rider information."""
from datetime import date

import re

import requests
from bs4 import BeautifulSoup


_RUSA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}
_MEMBERSEARCH_URL = 'https://rusa.org/cgi-bin/membersearch_PF.pl'
_EXPIRES_RE = re.compile(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})')


def _parse_membership_expires(raw: str | None) -> date | None:
    """Parse RUSA member-search expiry like ``2026/12/31``."""
    if not raw:
        return None
    match = _EXPIRES_RE.search(str(raw).strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _membership_current(expires: date | None, today: date | None = None) -> bool:
    today = today or date.today()
    return expires is not None and expires >= today


def get_rusa_membership_status(rusa_id, today: date | None = None):
    """Look up RUSA membership expiry from RUSA.org member search."""
    today = today or date.today()
    base = {
        'found': False,
        'rusa_id': str(rusa_id),
        'rusa_name': None,
        'city': None,
        'rusa_club': None,
        'membership_expires': None,
        'current': False,
        'error': None,
    }
    try:
        response = requests.get(
            _MEMBERSEARCH_URL,
            params={'mid': rusa_id},
            headers=_RUSA_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        target = str(rusa_id).strip()
        for row in soup.find_all('tr'):
            link = row.find('a', href=re.compile(rf'mid={re.escape(target)}\b'))
            if not link:
                continue
            cells = row.find_all('td')
            if len(cells) < 5:
                continue
            name_el = cells[1].find('b') or cells[1]
            expires = _parse_membership_expires(cells[4].get_text(strip=True))
            base.update({
                'found': True,
                'rusa_name': name_el.get_text(strip=True),
                'city': cells[2].get_text(strip=True) or None,
                'rusa_club': cells[3].get_text(strip=True) or None,
                'membership_expires': expires.isoformat() if expires else None,
                'current': _membership_current(expires, today),
                'error': None,
            })
            return base
        return {**base, 'error': f'RUSA ID {rusa_id} not found on RUSA.org'}
    except requests.RequestException as exc:
        return {**base, 'error': f'Error connecting to RUSA website: {exc}'}
    except Exception as exc:
        return {**base, 'error': f'Error fetching RUSA membership: {exc}'}


def normalize_last_name(last_name):
    """
    Convert last name to proper title case.
    Handles special cases like McDonald, O'Brien, etc.

    Args:
        last_name: Last name string (can be uppercase, lowercase, or mixed)

    Returns:
        str: Properly formatted last name in title case
    """
    if not last_name:
        return last_name

    # First, convert to title case
    name = last_name.strip().title()

    # Handle special prefixes (Mc, Mac, O')
    # McDonald, not Mcdonald
    name = re.sub(r'\bMc([a-z])', lambda m: f"Mc{m.group(1).upper()}", name)
    # MacDonald, not Macdonald
    name = re.sub(r'\bMac([a-z])', lambda m: f"Mac{m.group(1).upper()}", name)
    # O'Brien, not O'brien
    name = re.sub(r"\bO'([a-z])", lambda m: f"O'{m.group(1).upper()}", name)

    return name


def validate_rusa_id(rusa_id, first_name, last_name):
    """
    Validate RUSA ID by scraping the RUSA website.

    Args:
        rusa_id: RUSA member ID (integer or string)
        first_name: Rider's first name
        last_name: Rider's last name

    Returns:
        dict with keys:
        - valid: bool - True if name matches RUSA record
        - rusa_name: str - Name as it appears on RUSA (format: "LASTNAME, Firstname")
        - rusa_club: str - Club affiliation from RUSA
        - error: str - Error message if validation fails
    """
    try:
        url = f"https://rusa.org/cgi-bin/resultsearch_PF.pl?mid={rusa_id}&sortby=date"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        page_text = soup.get_text()

        # Pattern: "LASTNAME, Firstname | Club | [optional number]"
        # The RUSA ID is in the URL, not on the page
        pattern = r'([A-Z\s]+),\s+([A-Za-z\s]+)\s*\|\s*([^|]+?)\s*\|'
        matches = re.findall(pattern, page_text)

        if not matches:
            return {
                'valid': False,
                'error': f'RUSA ID {rusa_id} not found or page format not recognized',
                'rusa_name': None,
                'rusa_club': None
            }

        # Take the first match (should be the rider's info at the top)
        rusa_last, rusa_first, rusa_club = matches[0]
        rusa_last = rusa_last.strip()
        rusa_first = rusa_first.strip()
        rusa_club = rusa_club.strip()

        # Convert last name to proper title case
        normalized_last = normalize_last_name(rusa_last)
        rusa_name = f"{normalized_last}, {rusa_first}"

        # Normalize for comparison (case-insensitive)
        provided_last = last_name.strip().upper()
        provided_first = first_name.strip().lower()
        rusa_last_normalized = rusa_last.upper()
        rusa_first_normalized = rusa_first.lower()

        # Check if names match
        if provided_last == rusa_last_normalized and provided_first == rusa_first_normalized:
            return {
                'valid': True,
                'rusa_name': rusa_name,
                'rusa_club': rusa_club,
                'error': None
            }
        else:
            return {
                'valid': False,
                'error': f'Name mismatch. RUSA record shows: {rusa_name}',
                'rusa_name': rusa_name,
                'rusa_club': rusa_club
            }

    except requests.RequestException as e:
        return {
            'valid': False,
            'error': f'Error connecting to RUSA website: {str(e)}',
            'rusa_name': None,
            'rusa_club': None
        }
    except Exception as e:
        return {
            'valid': False,
            'error': f'Validation error: {str(e)}',
            'rusa_name': None,
            'rusa_club': None
        }


def get_rusa_name(rusa_id):
    """
    Get the name associated with a RUSA ID without validation.
    Returns the name as it appears on RUSA.org or None if not found.
    """
    try:
        url = f"https://rusa.org/cgi-bin/resultsearch_PF.pl?mid={rusa_id}&sortby=date"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        page_text = soup.get_text()

        # Pattern: "LASTNAME, Firstname | Club | [optional number]"
        pattern = r'([A-Z\s]+),\s+([A-Za-z\s]+)\s*\|\s*([^|]+?)\s*\|'
        matches = re.findall(pattern, page_text)

        if matches:
            rusa_last, rusa_first, rusa_club = matches[0]
            normalized_last = normalize_last_name(rusa_last.strip())
            return f"{normalized_last}, {rusa_first.strip()}"

        return None

    except Exception:
        return None


def get_rusa_info(rusa_id):
    """
    Fetch rider information from RUSA.org by RUSA ID.
    Returns dict with first_name, last_name, rusa_name, rusa_club, and valid flag.

    Args:
        rusa_id: RUSA member ID (integer or string)

    Returns:
        dict with keys:
        - valid: bool - True if RUSA ID found
        - first_name: str - First name
        - last_name: str - Last name (usually UPPERCASE)
        - rusa_name: str - Full name as "LASTNAME, Firstname"
        - rusa_club: str - Club affiliation
        - error: str - Error message if not found
    """
    try:
        url = f"https://rusa.org/cgi-bin/resultsearch_PF.pl?mid={rusa_id}&sortby=date"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        page_text = soup.get_text()

        # Pattern: "LASTNAME, Firstname | Club | [optional number]"
        # The RUSA ID is in the URL (mid parameter), not validated on the page
        pattern = r'([A-Z\s]+),\s+([A-Za-z\s]+)\s*\|\s*([^|]+?)\s*\|'
        matches = re.findall(pattern, page_text)

        if matches:
            rusa_last, rusa_first, rusa_club = matches[0]
            rusa_last = rusa_last.strip()
            rusa_first = rusa_first.strip()
            rusa_club = rusa_club.strip()

            # Convert last name to proper title case
            normalized_last = normalize_last_name(rusa_last)

            return {
                'valid': True,
                'first_name': rusa_first,
                'last_name': normalized_last,
                'rusa_name': f"{normalized_last}, {rusa_first}",
                'rusa_club': rusa_club,
                'error': None
            }

        return {
            'valid': False,
            'first_name': None,
            'last_name': None,
            'rusa_name': None,
            'rusa_club': None,
            'error': f'RUSA ID {rusa_id} not found on RUSA.org'
        }

    except requests.RequestException as e:
        return {
            'valid': False,
            'first_name': None,
            'last_name': None,
            'rusa_name': None,
            'rusa_club': None,
            'error': f'Error connecting to RUSA website: {str(e)}'
        }
    except Exception as e:
        return {
            'valid': False,
            'first_name': None,
            'last_name': None,
            'rusa_name': None,
            'rusa_club': None,
            'error': f'Error fetching RUSA information: {str(e)}'
        }
