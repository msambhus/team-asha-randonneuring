"""Pure RUSA brevet-stats helpers for the BrevetHub dashboard.

Framework-free and DB-free: turn the raw scrape from ``shared/rusa.py`` into a
JSON-safe list suitable for caching, and compute summary stats from a cached
list. Kept separate from the route so the cache orchestration stays thin and
these computations are unit-testable in isolation. Distance bands and the SR
rule mirror Team Asha's definitions (200/300/400/600 series; 600 tier = >=600),
sourced from the shared ``seasons`` module so the thresholds live in one place.
"""
from datetime import datetime

# Distance bands + SR-tier classification are single-sourced in shared.seasons so
# the dashboard, profile, and rides-by-season pages can never disagree.
from shared.seasons import DISTANCE_BANDS as _BANDS
from shared.seasons import band_for as _band_for
from shared.seasons import sr_tier_for as _sr_tier


def normalize_results(raw):
    """Convert ``shared/rusa.py`` results to JSON-safe brevet dicts for caching.

    Each raw result carries a ``datetime.date``; store it as an ISO
    ``YYYY-MM-DD`` string so it round-trips through JSONB. Rows without a usable
    distance are skipped.
    """
    brevets = []
    for r in raw or []:
        d = r.get('date')
        iso = d.isoformat() if hasattr(d, 'isoformat') else (d or None)
        try:
            distance_km = int(r.get('distance_km') or 0)
        except (TypeError, ValueError):
            continue
        brevets.append({
            'date': iso,
            'distance_km': distance_km,
            'finish_time': r.get('finish_time') or '',
            'route_name': r.get('route_name') or '',
            'event_type': r.get('event_type') or '',
        })
    return brevets


def _year_of(brevet):
    iso = brevet.get('date') or ''
    try:
        return int(iso[:4])
    except (TypeError, ValueError):
        return None


def compute_stats(brevets, current_year=None):
    """Summary stats over a JSON-safe brevet list (from :func:`normalize_results`).

    Returns total km + count, per-band counts, SR status (a full 200+300+400+600
    series within one calendar year), the longest brevet, and current-calendar-year
    totals. Season = calendar year (BrevetHub has no is_current season table).
    """
    if current_year is None:
        current_year = datetime.now().year

    total_km = 0
    longest_km = 0
    bands = {b: 0 for b in _BANDS}
    per_year_tiers = {}
    season_km = 0
    season_count = 0
    permanent_count = 0
    rides_1000_plus = 0

    for b in brevets:
        dist = b.get('distance_km') or 0
        total_km += dist
        if dist > longest_km:
            longest_km = dist
        if 'perm' in str(b.get('event_type') or '').lower() or 'perm' in str(b.get('route_name') or '').lower():
            permanent_count += 1
        if dist >= 1000:
            rides_1000_plus += 1
        band = _band_for(dist)
        if band is not None:
            bands[band] += 1
        yr = _year_of(b)
        tier = _sr_tier(dist)
        if yr is not None and tier is not None:
            per_year_tiers.setdefault(yr, {200: 0, 300: 0, 400: 0, 600: 0})[tier] += 1
        if yr == current_year:
            season_km += dist
            season_count += 1

    sr_year = None
    for yr, tiers in sorted(per_year_tiers.items()):
        if all(tiers[t] > 0 for t in (200, 300, 400, 600)):
            sr_year = yr  # keep the most recent qualifying year

    return {
        'total_km': total_km,
        'count': len(brevets),
        'bands': {str(b): bands[b] for b in _BANDS},
        'is_sr': sr_year is not None,
        'sr_year': sr_year,
        'longest_km': longest_km,
        'season_year': current_year,
        'season_total_km': season_km,
        'season_count': season_count,
        'permanent_count': permanent_count,
        'rides_1000_plus': rides_1000_plus,
    }
