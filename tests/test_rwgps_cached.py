"""fetch_route_cached — the fail-soft, memoized wrapper the plan render path uses.

`fetch_route` RAISES on missing credentials / 404 / 401 / 429 / network errors (it
never returns None), and `functools.lru_cache` does not cache exceptions. The plan
page is guest-readable, so a raise on the render path would be a 500. This guards the
three load-bearing properties of the wrapper: fail-soft to None on any raise, no fetch
attempted for a falsy route id, and a successful geometry fetch served from cache on
the second view.
"""
from unittest.mock import patch

import shared.rwgps as rwgps


def _clear_cache():
    rwgps._fetch_route_cached_inner.cache_clear()


def test_returns_none_when_fetch_raises_missing_creds():
    _clear_cache()
    with patch.object(rwgps, 'fetch_route',
                      side_effect=Exception('RWGPS API credentials not configured')) as m:
        assert rwgps.fetch_route_cached('12345') is None
        assert m.called  # a fetch WAS attempted for a real id


def test_returns_none_on_falsy_route_id_without_fetching():
    _clear_cache()
    with patch.object(rwgps, 'fetch_route') as m:
        assert rwgps.fetch_route_cached(None) is None
        assert rwgps.fetch_route_cached('') is None
        assert not m.called  # short-circuit — no HTTP attempt for a falsy id


def test_returns_none_on_404_and_network_errors():
    _clear_cache()
    for exc in (Exception('RWGPS route 999 not found.'),
                Exception('RWGPS API rate limited.'),
                ConnectionError('boom')):
        _clear_cache()
        with patch.object(rwgps, 'fetch_route', side_effect=exc):
            assert rwgps.fetch_route_cached('999') is None


def test_successful_fetch_is_served_from_cache_on_second_call():
    _clear_cache()
    route = {'track_points': [{'x': -122.0, 'y': 37.0, 'd': 0, 'e': 10}]}
    with patch.object(rwgps, 'fetch_route', return_value=route) as m:
        first = rwgps.fetch_route_cached('42', 'k', 't')
        second = rwgps.fetch_route_cached('42', 'k', 't')
        assert first is route and second is route
        assert m.call_count == 1  # second view served from the process cache
    _clear_cache()
