"""Tests for services/plan_match.py — the shared ride↔plan name matcher used by
both the web (routes/riders.py) and the mobile API (routes/live.py)."""
from services.plan_match import normalize_route, match_plan


_PLANS = [
    {'name': 'Mendocino Coast 600K', 'slug': 'mendocino-coast-600k'},
    {'name': 'SCR Surf City VI 600k #3141', 'slug': 'scr-surf-city-vi-600k-3141'},
    {'name': 'SCR HMB-Marina 300k', 'slug': 'scr-hmb-marina-300k'},
]


def test_matches_scr_600k_despite_null_fk():
    # The real SCR 600k case: ride name vs plan name resolve by keywords.
    m = match_plan('Surf City 600k VI Brevet (#3141)', _PLANS)
    assert m is not None and m['slug'] == 'scr-surf-city-vi-600k-3141'


def test_no_match_returns_none():
    assert match_plan('Totally Unrelated Gravel Grinder', _PLANS) is None


def test_requires_distinctive_word_not_just_generic():
    # Only the generic token '600k' in common → not enough to match.
    assert match_plan('Random 600k', [{'name': 'Other 600k', 'slug': 'x'}]) is None


def test_normalize_route_is_none_safe_and_strips_noise():
    assert normalize_route(None) == set()
    words = normalize_route('SCR Surf City VI 600k Brevet (#3141) 2026')
    assert 'surf' in words and 'city' in words
    assert 'brevet' not in words and '2026' not in words and 'scr' not in words
