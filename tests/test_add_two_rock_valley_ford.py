"""Tests for the SFR "Two Rock-Valley Ford 200km" seed script.

No real network or DB access — the SFR fetch and DB cursor are mocked.
"""
import importlib
from unittest.mock import patch, MagicMock

import pytest

import scripts.update_rusa_events as scraper
import scripts.add_sfr_two_rock_valley_ford as seed


class FakeCursor:
    """Minimal DB cursor: returns queued fetchone() results, records executes."""

    def __init__(self, fetchone_results, **kwargs):
        self._fetchone_results = list(fetchone_results)
        self.executed = []  # list of (sql, params)

    def execute(self, sql, params=None, **kwargs):
        self.executed.append((sql, params))

    def fetchone(self, **kwargs):
        return self._fetchone_results.pop(0)


# --- Import safety (the scraper bug fix) --------------------------------------

def test_scraper_import_safe_without_database_url(monkeypatch):
    """Importing the scraper with no DATABASE_URL must not SystemExit."""
    monkeypatch.delenv('DATABASE_URL', raising=False)
    # Reload so the module-level code re-runs under the cleared env.
    importlib.reload(scraper)
    assert scraper.DATABASE_URL is None


def test_seed_import_safe_without_database_url(monkeypatch):
    """Importing the seed script with no DATABASE_URL must not SystemExit."""
    monkeypatch.delenv('DATABASE_URL', raising=False)
    importlib.reload(seed)


# --- Fallback path ------------------------------------------------------------

REQUIRED_KEYS = {
    'name', 'ride_type', 'date', 'distance_km', 'distance_miles',
    'elevation_ft', 'rwgps_url', 'start_time', 'time_limit_hours',
    'start_location',
}


def test_fallback_event_has_all_keys_and_correct_values():
    """When the sheet yields nothing, fall back to the canonical hardcoded dict."""
    with patch.object(seed, 'download_sfr_events', return_value=[]):
        event = seed.find_target_event()

    # Every key upsert_event() reads is present.
    assert REQUIRED_KEYS.issubset(event.keys())
    assert event['name'] == 'Two Rock-Valley Ford 200km'
    assert event['date'] == '2026-06-06'
    assert event['distance_km'] == 200
    assert event['ride_type'] == 'ACP brevet'
    assert event['start_time'] == '7:00'
    assert event['time_limit_hours'] == 13.5
    # Unknown fields left None for the scraper to fill later.
    assert event['distance_miles'] is None
    assert event['elevation_ft'] is None
    assert event['rwgps_url'] is None
    assert event['start_location'] is None


def test_fallback_when_download_raises():
    """A network/parse error during the fetch falls back, not crashes."""
    with patch.object(seed, 'download_sfr_events', side_effect=Exception('boom')):
        event = seed.find_target_event()
    assert event['name'] == 'Two Rock-Valley Ford 200km'
    assert event['date'] == '2026-06-06'


def test_prefers_event_from_live_sheet_with_raw_date():
    """
    When present on the sheet, the scraped event (its exact name) wins.

    Regression guard: download_sfr_events() stores the sheet's *raw* non-ISO
    date (e.g. '6/6/2026'), not '2026-06-06'. Matching must key on the name,
    not an exact ISO date string, or the live-sheet branch is dead.
    """
    sheet_event = {
        'date': '6/6/2026',  # raw sheet format, deliberately NOT ISO
        'name': 'Two Rock-Valley Ford 200km',
        'distance_km': 200,
        'distance_miles': None,
        'elevation_ft': 1234,
        'rwgps_url': 'https://ridewithgps.com/routes/1',
        'start_time': '7:00',
        'time_limit_hours': 13.5,
        'start_location': 'Crissy Field',
    }
    other = dict(sheet_event, date='7/1/2026', name='Other 300km', distance_km=300)
    with patch.object(seed, 'download_sfr_events', return_value=[other, sheet_event]):
        event = seed.find_target_event()
    assert event is sheet_event  # adopts the sheet's exact name + raw date


def test_does_not_match_other_events():
    """An event without the Two Rock / 200k name markers is not matched."""
    other_200 = {'date': '6/6/2026', 'name': 'Marshall Wall 200km'}
    two_rock_other_dist = {'date': '5/2/2026', 'name': 'Two Rock-Valley Ford 300km'}
    with patch.object(seed, 'download_sfr_events',
                      return_value=[other_200, two_rock_other_dist]):
        event = seed.find_target_event()
    # No genuine match -> canonical fallback.
    assert event['name'] == 'Two Rock-Valley Ford 200km'
    assert event['date'] == '2026-06-06'


# --- upsert routing + idempotency ---------------------------------------------

def test_main_upserts_for_san_francisco(monkeypatch):
    """main() resolves DATABASE_URL, then upserts under the SFR region."""
    monkeypatch.setenv('DATABASE_URL', 'postgresql://localhost/test')

    fake_conn = MagicMock()
    fake_conn.cursor.return_value = MagicMock()

    with patch.object(seed, 'download_sfr_events', return_value=[]), \
         patch.object(seed.psycopg2, 'connect', return_value=fake_conn), \
         patch.object(seed, 'upsert_event', return_value='inserted') as mock_upsert:
        seed.main()

    assert mock_upsert.call_count == 1
    args, _ = mock_upsert.call_args
    # (cursor, region, event)
    assert args[1] == 'San Francisco'
    assert args[2]['name'] == 'Two Rock-Valley Ford 200km'
    fake_conn.commit.assert_called_once()


def test_main_exits_nonzero_when_upsert_errors(monkeypatch):
    """If the upsert resolves no club/season ('error'), main() must not report success."""
    monkeypatch.setenv('DATABASE_URL', 'postgresql://localhost/test')
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = MagicMock()

    with patch.object(seed, 'download_sfr_events', return_value=[]), \
         patch.object(seed.psycopg2, 'connect', return_value=fake_conn), \
         patch.object(seed, 'upsert_event', return_value='error'):
        with pytest.raises(SystemExit) as exc:
            seed.main()

    assert exc.value.code == 1
    fake_conn.commit.assert_not_called()  # nothing written -> no commit


def test_upsert_inserts_when_absent():
    """First run: no existing row -> INSERT with UPCOMING status."""
    cur = FakeCursor([
        (10,),    # club lookup
        (20,),    # season lookup
        None,     # existing ride lookup -> none
    ])
    action = scraper.upsert_event(cur, 'San Francisco', dict(seed.FALLBACK_EVENT))
    assert action == 'inserted'
    insert_sql = cur.executed[-1][0]
    assert 'INSERT INTO ride' in insert_sql
    assert 'UPCOMING' in cur.executed[-1][1]


def test_upsert_idempotent_on_second_run():
    """Second run: matching row exists (UPCOMING) -> UPDATE, never a 2nd insert."""
    cur = FakeCursor([
        (10,),            # club lookup
        (20,),            # season lookup
        (99, 'UPCOMING'),  # existing ride row (id, event_status)
    ])
    action = scraper.upsert_event(cur, 'San Francisco', dict(seed.FALLBACK_EVENT))
    assert action == 'updated'
    assert all('INSERT INTO ride' not in sql for sql, _ in cur.executed)
    assert 'UPDATE ride' in cur.executed[-1][0]


def test_upsert_skips_completed_row():
    """A finished event is never overwritten by the seed."""
    cur = FakeCursor([
        (10,),
        (20,),
        (99, 'COMPLETED'),
    ])
    action = scraper.upsert_event(cur, 'San Francisco', dict(seed.FALLBACK_EVENT))
    assert action == 'skipped'


def test_target_event_is_not_filtered():
    """200km has a standard ACP time limit, so it is never silently filtered."""
    assert scraper.get_time_limit_hours(200) == 13.5
