"""Unit tests for the shared, club-agnostic RUSA calendar engine.

No real network — ``shared.rusa_calendar.urlopen`` is patched. These prove the
faithfully-extracted parser's contract that both Team Asha and BrevetHub depend on:
  - the national event table parses into event dicts (incl. ``route_id``),
  - the national feed carries NO start location/time, so every event has
    ``start_location is None`` and ``start_time is None`` (never fabricated),
  - ``get_rwgps_url_from_route`` yields ONLY the RideWithGPS href (or None),
  - ``region_filter`` scopes/tags: None keeps every ACP/RUSA brevet with the raw
    region label; a dict narrows to its keys and maps the tag.
"""
from io import BytesIO
from unittest.mock import patch

import shared.rusa_calendar as rc


# --- A minimal RUSA event-search table (the printer-friendly HTML shape) ------
# Columns: Region | Type | Date | Distance | Climbing | Route | Website
_RUSA_HTML = """
<html><body><table>
<tr><th>Region</th><th>Type</th><th>Date</th><th>Distance</th><th>Climbing</th><th>Route</th><th>Web</th></tr>
<tr><td>CA: San Francisco</td><td>ACP brevet</td><td>2026/07/11</td><td>300</td><td></td>
    <td><a href="/cgi-bin/routeview_PF.pl?rtid=5678">Boonville Lollipop</a></td><td>info</td></tr>
<tr><td>CA: Davis</td><td>ACP brevet</td><td>2026/03/21</td><td>300</td><td>3,519'</td>
    <td><a href="/cgi-bin/routeview_PF.pl?rtid=1234">Davis Dunnigan Delta 302k</a></td><td>info</td></tr>
<tr><td>CA: San Francisco</td><td>RUSA populaire</td><td>2026/05/03</td><td>100</td><td></td>
    <td>Laguna Lake</td><td>info</td></tr>
<tr><td>CO: Boulder</td><td>RUSA brevet</td><td>2026/07/11</td><td>200</td><td></td>
    <td>Vail Pass Volley</td><td>info</td></tr>
</table></body></html>
"""


def _fake_urlopen(*args, **kwargs):
    return BytesIO(_RUSA_HTML.encode("utf-8"))


def test_national_table_parses_to_event_dicts_with_route_id():
    with patch.object(rc, "urlopen", _fake_urlopen):
        events = rc.get_rusa_events(fetch_rwgps=False)  # region_filter=None → all

    by_name = {e["name"]: e for e in events}
    # Every ACP/RUSA brevet across all regions is kept; the populaire is dropped.
    assert set(by_name) == {"Boonville Lollipop", "Davis Dunnigan Delta 302k", "Vail Pass Volley"}

    davis = by_name["Davis Dunnigan Delta 302k"]
    assert str(davis["date"]) == "2026-03-21"
    assert davis["distance_km"] == 300
    assert davis["time_limit_hours"] == 20          # standard ACP 300k limit
    assert davis["ride_type"] == "ACP brevet"
    assert davis["route_id"] == "1234"              # route id parsed from the Route cell
    assert davis["elevation_ft"] == 3519            # from the Climbing column


def test_national_feed_has_no_start_location_or_time():
    """The national feed carries neither — every event must expose None, never a
    fabricated value."""
    with patch.object(rc, "urlopen", _fake_urlopen):
        events = rc.get_rusa_events(fetch_rwgps=False)

    assert events  # guard: the fixture produced events
    for e in events:
        assert e["start_location"] is None
        assert e["start_time"] is None


def test_region_filter_none_keeps_all_and_uses_raw_label():
    with patch.object(rc, "urlopen", _fake_urlopen):
        events = rc.get_rusa_events(fetch_rwgps=False, region_filter=None)

    boonville = next(e for e in events if e["name"] == "Boonville Lollipop")
    # No mapping supplied → the raw RUSA region label is used as-is.
    assert boonville["region"] == "CA: San Francisco"
    # Boulder (a different region) is still present since None keeps all regions.
    assert any(e["name"] == "Vail Pass Volley" for e in events)


def test_region_filter_dict_narrows_and_maps_tag():
    region_map = {"CA: San Francisco": "San Francisco", "CA: Davis": "Davis"}
    with patch.object(rc, "urlopen", _fake_urlopen):
        events = rc.get_rusa_events(fetch_rwgps=False, region_filter=region_map)

    names = {e["name"] for e in events}
    # Only the two mapped CA regions survive; Boulder is filtered out.
    assert names == {"Boonville Lollipop", "Davis Dunnigan Delta 302k"}
    boonville = next(e for e in events if e["name"] == "Boonville Lollipop")
    assert boonville["region"] == "San Francisco"   # mapped value, not the raw label


# --------------------------------------------------------------------------- #
# get_rwgps_url_from_route — the route-detail page yields ONLY the RWGPS href.
# --------------------------------------------------------------------------- #
_ROUTE_HTML_WITH_RWGPS = (
    '<html><body>'
    '<a href="https://ridewithgps.com/routes/123456">View on RideWithGPS</a>'
    '<p>Start: somewhere secret</p>'
    '</body></html>'
)
_ROUTE_HTML_NO_RWGPS = '<html><body><p>No route link yet.</p></body></html>'


def test_get_rwgps_url_returns_href_only():
    with patch.object(rc, "urlopen",
                      lambda *a, **k: BytesIO(_ROUTE_HTML_WITH_RWGPS.encode("utf-8"))):
        url = rc.get_rwgps_url_from_route("5678")
    # Only the RideWithGPS href — never a start location parsed off the page.
    assert url == "https://ridewithgps.com/routes/123456"


def test_get_rwgps_url_none_when_absent():
    with patch.object(rc, "urlopen",
                      lambda *a, **k: BytesIO(_ROUTE_HTML_NO_RWGPS.encode("utf-8"))):
        assert rc.get_rwgps_url_from_route("5678") is None


def test_get_rwgps_url_none_on_network_error():
    def _boom(*a, **k):
        raise OSError("network down")
    with patch.object(rc, "urlopen", _boom):
        assert rc.get_rwgps_url_from_route("5678") is None


def test_get_rusa_events_returns_empty_on_error():
    def _boom(*a, **k):
        raise OSError("network down")
    with patch.object(rc, "urlopen", _boom):
        assert rc.get_rusa_events(fetch_rwgps=False) == []


def test_time_limit_hours_standard_ladder():
    assert rc.get_time_limit_hours(200) == 13.5
    assert rc.get_time_limit_hours(300) == 20
    assert rc.get_time_limit_hours(400) == 27
    assert rc.get_time_limit_hours(600) == 40
    assert rc.get_time_limit_hours(1000) == 75
    assert rc.get_time_limit_hours(111) is None
