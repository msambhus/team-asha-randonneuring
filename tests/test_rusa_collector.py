"""Tests for the RUSA multi-region event collector.

No real network or DB access — the RUSA fetch and DB cursor are mocked.
Covers:
  - get_rusa_events: parses the national search, keeps only team-region
    ACP/RUSA brevets, and tags each event with its club.region.
  - upsert_event: dedups by (date, name) OR (date, club, distance) so the same
    ride from two sources with different names updates one row, and COALESCE
    keeps existing enrichment when a sparse source has none.
"""
from io import BytesIO
from unittest.mock import patch

import scripts.update_rusa_events as scraper
# The national-feed parser now lives in the shared engine; the scraper is a thin
# importer. So the RUSA fetch is patched on shared.rusa_calendar (where urlopen
# actually lives), while upsert_event stays in scripts.update_rusa_events.
import shared.rusa_calendar as rusa_calendar


# --- A minimal RUSA event-search table (the printer-friendly HTML shape) ------
# Columns: Region | Type | Date | Distance | Climbing | Route | Website
_RUSA_HTML = """
<html><body><table>
<tr><th>Region</th><th>Type</th><th>Date</th><th>Distance</th><th>Climbing</th><th>Route</th><th>Web</th></tr>
<tr><td>CA: San Francisco</td><td>ACP brevet</td><td>2026/07/11</td><td>300</td><td></td>
    <td>Boonville Lollipop</td><td>info</td></tr>
<tr><td>CA: Davis</td><td>ACP brevet</td><td>2026/03/21</td><td>300</td><td>3,519'</td>
    <td><a href="/cgi-bin/routedetail.pl?rtid=1234">Davis Dunnigan Delta 302k</a></td><td>info</td></tr>
<tr><td>CA: San Francisco</td><td>RUSA populaire</td><td>2026/05/03</td><td>100</td><td></td>
    <td>Laguna Lake</td><td>info</td></tr>
<tr><td>CO: Boulder</td><td>ACP brevet</td><td>2026/07/11</td><td>200</td><td></td>
    <td>Vail Pass Volley</td><td>info</td></tr>
</table></body></html>
"""


def _fake_urlopen(*args, **kwargs):
    return BytesIO(_RUSA_HTML.encode("utf-8"))


def test_get_rusa_events_keeps_team_brevets_only():
    """Team-region ACP/RUSA brevets are kept; populaires and other states drop."""
    with patch.object(rusa_calendar, "urlopen", _fake_urlopen):
        events = scraper.get_rusa_events(fetch_rwgps=False)

    names = {e["name"] for e in events}
    # Boonville (SF brevet) and the Davis brevet are kept.
    assert "Boonville Lollipop" in names
    assert "Davis Dunnigan Delta 302k" in names
    # The SF populaire is dropped (wrong type); Boulder is dropped (not a team region).
    assert "Laguna Lake" not in names
    assert "Vail Pass Volley" not in names
    assert len(events) == 2


def test_get_rusa_events_tags_club_region_and_fields():
    """Boonville is tagged with the SF club.region and parsed correctly."""
    with patch.object(rusa_calendar, "urlopen", _fake_urlopen):
        events = scraper.get_rusa_events(fetch_rwgps=False)

    boonville = next(e for e in events if e["name"] == "Boonville Lollipop")
    assert boonville["region"] == "San Francisco"   # club.region for club_id lookup
    assert str(boonville["date"]) == "2026-07-11"
    assert boonville["distance_km"] == 300
    assert boonville["time_limit_hours"] == 20       # standard ACP 300k limit
    assert boonville["elevation_ft"] is None         # no route assigned yet → blank
    assert boonville["rwgps_url"] is None             # fetch_rwgps=False


class _FakeCursor:
    """Minimal cursor: returns queued fetchone() results, records executes."""

    def __init__(self, fetchone_results):
        self._fetchone_results = list(fetchone_results)
        self.executed = []

    def execute(self, sql, params=None, **kwargs):
        self.executed.append((sql, params))

    def fetchone(self, **kwargs):
        return self._fetchone_results.pop(0)


_BOONVILLE = {
    "date": "2026-07-11", "name": "Boonville Lollipop", "distance_km": 300,
    "distance_miles": None, "elevation_ft": None, "rwgps_url": None,
    "start_time": None, "time_limit_hours": 20, "start_location": None,
    "ride_type": "ACP brevet", "region": "San Francisco",
}


def test_upsert_inserts_when_no_match():
    # club lookup -> (1,), season lookup -> (5,), existence -> None
    cur = _FakeCursor([(1,), (5,), None])
    action = scraper.upsert_event(cur, "San Francisco", dict(_BOONVILLE))
    assert action == "inserted"
    assert any("INSERT INTO ride" in sql for sql, _ in cur.executed)


def test_upsert_existence_check_matches_date_club_or_distance():
    """The dedup query keys on (date, name) OR (date, club_id, distance_km),
    and its params are in the exact order the placeholders expect."""
    cur = _FakeCursor([(1,), (5,), (42, "UPCOMING")])
    scraper.upsert_event(cur, "San Francisco", dict(_BOONVILLE))
    # Find the existence SELECT (the one selecting event_status).
    sel_sql, sel_params = next(
        (sql, p) for sql, p in cur.executed if "event_status" in sql and "SELECT" in sql.upper()
    )
    assert "ri.club_id = %s" in sel_sql and "ri.distance_km = %s" in sel_sql
    # club lookup returned (1,), so club_id == 1; distance 300; name twice.
    assert sel_params == ("2026-07-11", "Boonville Lollipop", 1, 300, "Boonville Lollipop")


def test_upsert_error_when_club_missing():
    """A missing club row returns 'error' (and must not raise) so the daily
    run can keep going / report it rather than crash."""
    cur = _FakeCursor([None])  # club lookup -> no row
    assert scraper.upsert_event(cur, "Nowhere", dict(_BOONVILLE)) == "error"


def test_upsert_update_coalesces_soft_fields():
    """An update must not wipe existing enrichment with NULLs (COALESCE)."""
    cur = _FakeCursor([(1,), (5,), (42, "UPCOMING")])
    action = scraper.upsert_event(cur, "San Francisco", dict(_BOONVILLE))
    assert action == "updated"
    upd = next(sql for sql, _ in cur.executed if sql.strip().upper().startswith("UPDATE RIDE"))
    for col in ("rwgps_url", "elevation_ft", "start_time", "start_location"):
        assert f"{col} = COALESCE(%s, {col})" in upd


def test_upsert_skips_completed():
    cur = _FakeCursor([(1,), (5,), (42, "COMPLETED")])
    action = scraper.upsert_event(cur, "San Francisco", dict(_BOONVILLE))
    assert action == "skipped"


def test_upsert_filters_nonstandard_distance():
    cur = _FakeCursor([])  # never reaches club lookup
    ev = dict(_BOONVILLE, distance_km=111)  # no standard ACP time limit
    assert scraper.upsert_event(cur, "San Francisco", ev) == "filtered"
