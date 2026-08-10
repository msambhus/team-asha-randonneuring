"""RUSA calendar sync — region filtering."""
from unittest.mock import patch

from brevethub.routes.calendar import _filter_events_by_region, _scrape_and_upsert

_SCRAPED = [
    {'route_id': '1', 'name': 'SFR 200', 'date': '2026-08-15', 'distance_km': 200,
     'region': 'CA: San Francisco', 'ride_type': 'ACP brevet'},
    {'route_id': '2', 'name': 'Davis 300', 'date': '2026-09-01', 'distance_km': 300,
     'region': 'CA: Davis', 'ride_type': 'ACP brevet'},
]


def test_filter_events_by_region_keeps_matching_only():
    filtered = _filter_events_by_region(_SCRAPED, 'CA: San Francisco')
    assert len(filtered) == 1
    assert filtered[0]['name'] == 'SFR 200'


def test_filter_events_by_region_none_returns_all():
    assert _filter_events_by_region(_SCRAPED, None) == _SCRAPED


def test_scrape_and_upsert_filters_by_region_prefix():
    with patch('brevethub.routes.calendar.get_rusa_events', return_value=_SCRAPED) as mock_scrape, \
         patch('brevethub.routes.calendar.models.upsert_brevet_event') as mock_upsert:
        count = _scrape_and_upsert(region_prefix='CA: Davis')
    mock_scrape.assert_called_once()
    assert count == 1
    mock_upsert.assert_called_once_with(_SCRAPED[1])
