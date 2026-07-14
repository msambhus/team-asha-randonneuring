"""Dev-only: regenerate the RUSA club seed used by migration 033.

RUSA publishes its official member-club directory. This script scrapes that
directory and emits `INSERT ... ON CONFLICT DO NOTHING` rows for the `rp_club`
seed block in `migrations/033_brevethub_rp_tables.sql`.

It is NOT run at deploy time and is not imported by the app — the migration
carries a committed snapshot so `033` is self-contained and reproducible. Run it
by hand to refresh the list, then paste the output into the migration's seed
section.

    python3 scripts/fetch_rusa_clubs.py > /tmp/rp_club_seed.sql

Uses only the shared, club-agnostic scraping stack (requests + BeautifulSoup);
it reads rusa.org, never any Team Asha table.
"""
import sys

import requests
from bs4 import BeautifulSoup

# RUSA's official club directory. The exact table markup changes over time, so
# treat this as a starting point and eyeball the output before committing.
RUSA_CLUBS_URL = "https://rusa.org/cgi-bin/clublist_PF.pl"


def _sql_str(value):
    """Quote a value for SQL, or NULL when empty."""
    if value is None or value == '':
        return 'NULL'
    return "'" + value.replace("'", "''") + "'"


def fetch_clubs():
    """Return a list of {rusa_club_id, name, city, state} dicts, best-effort."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    resp = requests.get(RUSA_CLUBS_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, 'html.parser')
    table = soup.find('table')
    clubs = []
    if not table:
        return clubs

    for row in table.find_all('tr'):
        cells = [c.get_text(strip=True) for c in row.find_all('td')]
        if len(cells) < 2:
            continue
        # Layout varies; adjust indices to the current directory before use.
        rusa_club_id, name = cells[0], cells[1]
        city = cells[2] if len(cells) > 2 else ''
        state = cells[3] if len(cells) > 3 else ''
        if not name or name.lower() == 'name':
            continue
        clubs.append({
            'rusa_club_id': rusa_club_id,
            'name': name,
            'city': city,
            'state': state,
        })
    return clubs


def main():
    clubs = fetch_clubs()
    if not clubs:
        print("-- No clubs parsed; inspect the RUSA directory markup.", file=sys.stderr)
    for c in clubs:
        print(
            "INSERT INTO rp_club (rusa_club_id, name, city, state) VALUES ("
            f"{_sql_str(c['rusa_club_id'])}, {_sql_str(c['name'])}, "
            f"{_sql_str(c['city'])}, {_sql_str(c['state'])}) "
            "ON CONFLICT (rusa_club_id) DO NOTHING;"
        )


if __name__ == '__main__':
    main()
