"""Pure randonneuring season / SR / R-12 logic shared by Team Asha's engines and
BrevetHub.

Framework-free and DB-free by contract (enforced by
``tests/brevethub/test_shared_isolation.py``): no Flask, no app globals, and
crucially **no clock reads** — every date-relative computation takes an explicit
``today`` so results are deterministic and unit-testable. BrevetHub ships a
byte-identical copy at ``brevethub/shared/seasons.py`` kept in sync by
``test_vendored_shared_sync``.

Definitions mirror Team Asha's (reused here, never imported from its app):

  - **Season boundary.** The randonneuring season runs Nov 1 – Oct 31 and is named
    ``"YYYY-YYYY"`` for the November→October window it spans. A ride on 2024-11-15
    and one on 2025-03-01 both belong to the ``"2024-2025"`` season; 2025-10-31 is
    still ``"2024-2025"`` while 2025-11-01 opens ``"2025-2026"``. (Team Asha stores
    per-season start/end dates in its ``season`` table; BrevetHub derives the same
    Nov 1 boundary from ride dates instead of a per-club table.)
  - **SR series tiers.** 200 (200–299), 300 (300–399), 400 (400–599), 600 (>=600);
    a >=600 ride (a 1000 included) satisfies the 600 leg. A Super Randonneur is all
    four legs within one season.
  - **Display bands.** 200/300/400/600/1000 where the 600 band is [600, 1000) and
    the 1000 band is >=1000.
  - **R-12.** At least one >=200 km ride per calendar month for 12 consecutive
    months; non-overlapping 12-month blocks each earn one award. The *current
    streak* is the trailing run of consecutive qualifying months, "active" when its
    last month is the current or previous calendar month relative to ``today``.

Every function accepts brevets in the JSON-safe shape BrevetHub caches (see
``brevethub.rusa_stats.normalize_results``): dicts with a ``date`` (ISO
``YYYY-MM-DD`` string, or a ``date``/``datetime``) and a ``distance_km`` int.
"""
from datetime import date

from shared.rusa_ride_kind import ride_kind_counts, rusa_ride_kind

# Randonneuring season boundary: a new season starts on November 1.
SEASON_START_MONTH = 11

# SR series legs (km) and display bands (km) — the single source of truth reused
# by ``brevethub/rusa_stats.py`` so the two never drift.
SR_TIERS = (200, 300, 400, 600)
DISTANCE_BANDS = (200, 300, 400, 600, 1000)

# Paris-Brest-Paris detection. PBP is the 1200 km ACP grande randonnee held every
# four years; a finisher earns the title "Ancien"/"Ancienne". A record is treated
# as PBP when its event name contains one of these fragments AND its distance
# clears the floor (1200 km, with headroom for how sources round a grande
# randonnee's official distance).
PBP_NAME_FRAGMENTS = ('paris-brest', 'pbp')
PBP_MIN_KM = 1100


def sr_tier_for(distance_km):
    """SR series leg for a distance, or ``None`` if under 200 km. The 600 leg is
    ``>=600``, so a 1000 km ride also satisfies the 600 requirement — matching
    Team Asha's single-sourced SR rule."""
    if 200 <= distance_km < 300:
        return 200
    if 300 <= distance_km < 400:
        return 300
    if 400 <= distance_km < 600:
        return 400
    if distance_km >= 600:
        return 600
    return None


def band_for(distance_km):
    """Display band for a distance, or ``None`` if under 200 km. The 600 band is
    ``[600, 1000)``; the 1000 band is ``>=1000``."""
    if 200 <= distance_km < 300:
        return 200
    if 300 <= distance_km < 400:
        return 300
    if 400 <= distance_km < 600:
        return 400
    if 600 <= distance_km < 1000:
        return 600
    if distance_km >= 1000:
        return 1000
    return None


def _coerce_date(value):
    """Coerce a brevet ``date`` (ISO ``YYYY-MM-DD`` string, or a ``date`` /
    ``datetime``) to a plain ``date``, or ``None`` if it can't be parsed."""
    if value is None:
        return None
    if isinstance(value, date):  # datetime is a date subclass; normalize to date
        return date(value.year, value.month, value.day)
    try:
        y, m, d = str(value)[:10].split('-')
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def earliest_brevet_date(brevets):
    """Earliest parseable brevet date in a rider's RUSA history, or ``None``."""
    earliest = None
    for b in brevets or []:
        d = _coerce_date(b.get('date'))
        if d is None:
            continue
        if earliest is None or d < earliest:
            earliest = d
    return earliest


def season_name_for_date(value):
    """The ``"YYYY-YYYY"`` randonneuring season a date falls in (Nov 1 boundary),
    or ``None`` if the date can't be parsed."""
    d = _coerce_date(value)
    if d is None:
        return None
    start_year = d.year if d.month >= SEASON_START_MONTH else d.year - 1
    return f'{start_year}-{start_year + 1}'


def current_season_name(today):
    """The season name for ``today``. The caller passes the date — this module
    never reads the clock, so callers stay deterministic and testable."""
    return season_name_for_date(today)


def group_brevets_by_season(brevets):
    """Group brevets into randonneuring seasons, newest season first and each
    season's brevets newest-ride first.

    Returns a list of ``{'season': 'YYYY-YYYY', 'brevets': [...]}``. Brevets whose
    date does not parse are dropped from the grouping (they can't be placed)."""
    by_season = {}
    for b in brevets or []:
        name = season_name_for_date(b.get('date'))
        if name is None:
            continue
        by_season.setdefault(name, []).append(b)
    result = []
    for name in sorted(by_season, reverse=True):
        rides = sorted(by_season[name], key=lambda x: x.get('date') or '', reverse=True)
        result.append({'season': name, 'brevets': rides})
    return result


def sr_progress(brevets):
    """Which SR legs (200/300/400/600) a set of brevets covers, how many rides
    land in each leg, and how many *complete* Super Randonneur series they form.

    ``counts`` is the number of rides in each SR tier (a >=600 ride, a 1000
    included, counts once toward the 600 tier and never toward 400). ``sr_count``
    is the number of complete {200, 300, 400, 600} sets = ``min`` across the four
    tier counts, so a rider who rides the whole series twice in a season scores
    ``sr_count == 2``. ``is_sr`` stays ``sr_count >= 1`` (all four legs present).

    Returns ``{'legs': {200: bool, ...}, 'counts': {200: int, ...},
    'is_sr': bool, 'sr_count': int}``. ``legs`` and ``is_sr`` are unchanged from
    the original boolean contract so existing callers keep working.
    """
    counts = {t: 0 for t in SR_TIERS}
    for b in brevets or []:
        tier = sr_tier_for(b.get('distance_km') or 0)
        if tier is not None:
            counts[tier] += 1
    legs = {t: counts[t] > 0 for t in SR_TIERS}
    return {
        'legs': legs,
        'counts': counts,
        'is_sr': all(legs.values()),
        'sr_count': min(counts.values()),
    }


def season_summary(brevets):
    """Per-season roll-up: total km, ride count, per-band counts, and SR progress.

    ``bands`` is keyed by ``str`` (``'200'``…``'1000'``) for direct template access,
    matching ``brevethub.rusa_stats.compute_stats``."""
    total_km = 0
    bands = {b: 0 for b in DISTANCE_BANDS}
    for b in brevets or []:
        dist = b.get('distance_km') or 0
        total_km += dist
        band = band_for(dist)
        if band is not None:
            bands[band] += 1
    sr = sr_progress(brevets)
    return {
        'total_km': total_km,
        'count': len(brevets or []),
        'bands': {str(b): bands[b] for b in DISTANCE_BANDS},
        'legs': sr['legs'],
        'is_sr': sr['is_sr'],
        'sr_count': sr['sr_count'],
    }


def seasons_with_summaries(brevets, today):
    """Group brevets by season (newest first) with each season's summary and an
    ``is_current`` flag for the season ``today`` falls in. Drives the
    rides-by-season page directly."""
    current = current_season_name(today)
    out = []
    for group in group_brevets_by_season(brevets):
        rides = [
            {**ride, 'ride_kind': rusa_ride_kind(ride)}
            for ride in group['brevets']
        ]
        out.append({
            'season': group['season'],
            'brevets': rides,
            'summary': season_summary(rides),
            'is_current': group['season'] == current,
        })
    return out


def _qualifying_months(brevets):
    """Sorted distinct ``(year, month)`` tuples holding at least one >=200 km
    brevet — the monthly qualification rule shared by R-12 awards and streak."""
    months = set()
    for b in brevets or []:
        if (b.get('distance_km') or 0) < 200:
            continue
        d = _coerce_date(b.get('date'))
        if d is not None:
            months.add((d.year, d.month))
    return sorted(months)


def _month_diff(a, b):
    """Whole months from ``(year, month)`` a to b."""
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def r12_awards(brevets):
    """R-12 awards: one per non-overlapping run of 12 consecutive qualifying
    months. Returns a list of ``{'start_month', 'end_month'}`` (``'YYYY-MM'``),
    oldest first. Mirrors Team Asha's non-overlapping 12-block slicing."""
    months = _qualifying_months(brevets)
    if not months:
        return []
    awards = []
    run_start = 0
    for i in range(1, len(months) + 1):
        # A run ends at a gap between consecutive months or at the list's end.
        if i == len(months) or _month_diff(months[i - 1], months[i]) != 1:
            run_len = i - run_start
            j = 0
            while j + 12 <= run_len:
                s = months[run_start + j]
                e = months[run_start + j + 11]
                awards.append({
                    'start_month': f'{s[0]}-{s[1]:02d}',
                    'end_month': f'{e[0]}-{e[1]:02d}',
                })
                j += 12
            run_start = i
    return awards


def r12_current_streak(brevets, today):
    """The rider's *current* R-12 streak: the trailing run of consecutive
    qualifying months ending at the most recent qualifying month.

    Returns ``{'months': int, 'active': bool}``. ``active`` means the streak is
    still live — its last qualifying month is the current or previous calendar
    month relative to ``today`` — so the rider can keep it going."""
    months = _qualifying_months(brevets)
    if not months:
        return {'months': 0, 'active': False}
    streak = 1
    for i in range(len(months) - 1, 0, -1):
        if _month_diff(months[i - 1], months[i]) == 1:
            streak += 1
        else:
            break
    t = _coerce_date(today)
    active = t is not None and _month_diff(months[-1], (t.year, t.month)) <= 1
    return {'months': streak, 'active': active}


def career_summary(brevets, today):
    """Whole-history rider summary for the profile page, computed on the same
    Nov 1 season boundary as the rides-by-season view.

    Returns career totals, the current season's SR progress, every season that was
    an SR, the total SR awards across the career (``total_sr``, which counts every
    complete series — a season with two full series contributes 2), and R-12
    (awards earned + current streak). ``sr_seasons``/``is_sr`` are unchanged:
    ``sr_seasons`` still lists the seasons with at least one SR, so
    ``len(sr_seasons)`` remains the count of SR *seasons* while ``total_sr`` is the
    count of SR *awards*."""
    seasons = seasons_with_summaries(brevets, today)
    current = current_season_name(today)
    current_season = next((s for s in seasons if s['season'] == current), None)
    sr_seasons = [s['season'] for s in seasons if s['summary']['is_sr']]
    total_sr = sum(s['summary']['sr_count'] for s in seasons)
    kinds = ride_kind_counts(brevets)
    return {
        'total_km': sum((b.get('distance_km') or 0) for b in brevets or []),
        'count': len(brevets or []),
        'brevet_count': kinds['brevet'],
        'permanent_count': kinds['permanent'],
        'populaire_count': kinds['populaire'],
        'current_season': current,
        'current_sr': current_season['summary'] if current_season else sr_progress([]),
        'sr_seasons': sr_seasons,
        'total_sr': total_sr,
        'is_sr': bool(sr_seasons),
        'r12_awards': r12_awards(brevets),
        'r12_streak': r12_current_streak(brevets, today),
    }


def pbp_ancien_years(brevets):
    """Sorted distinct years in which the rider FINISHED Paris-Brest-Paris, earning
    the title Ancien(ne). Returns ``[]`` for a rider with no PBP finish.

    Detection is by EVENT NAME: a record whose ``route_name`` contains
    ``"paris-brest"`` or ``"pbp"`` (case-insensitive) and whose distance is at
    least ``PBP_MIN_KM`` km. Every record in the RUSA history is a finished result,
    so a matching record is a finish. Name-matching is preferred over a pure
    year+distance rule because other ~1200 km grandes randonnees exist
    (Boston-Montreal-Boston, London-Edinburgh-London, ...); matching on the name
    keeps those from being mislabelled as PBP.

    Limitation / fallback: if a data source ever stores PBP records without a
    usable ``route_name``, name-matching yields ``[]`` (a miss, not a false hit).
    The documented fallback — a >=1100 km finish dated in a PBP year
    (2019/2023/2027...) — is intentionally NOT enabled here, because it would
    misattribute those other 1200 km randonnees to PBP. Callers that know their
    source lacks event names can layer that fallback on top with that
    false-positive risk in mind; this helper stays conservative by default.
    """
    years = set()
    for b in brevets or []:
        if (b.get('distance_km') or 0) < PBP_MIN_KM:
            continue
        name = str(b.get('route_name') or '').lower()
        if any(fragment in name for fragment in PBP_NAME_FRAGMENTS):
            d = _coerce_date(b.get('date'))
            if d is not None:
                years.add(d.year)
    return sorted(years)


def ranked_awards(brevets, today=None):
    """Return the strongest achievements derivable from the cached RUSA history.

    RUSA publishes the award definitions, but the result feed does not expose a
    complete award ledger for every rider.  These are therefore conservative,
    history-derived counts (never claims based on a missing trophy record),
    ordered for profile display rather than official award precedence.
    """
    rides = list(brevets or [])
    career = career_summary(rides, today)
    r12_count = len(career['r12_awards'])
    by_year = {}
    for ride in rides:
        d = _coerce_date(ride.get('date'))
        if d is not None:
            by_year[d.year] = by_year.get(d.year, 0) + (ride.get('distance_km') or 0)
    k_hounds = sum(1 for km in by_year.values() if km >= 10000)
    ultra_k_hounds = k_hounds // 10
    ultra_randonneurs = career['total_sr'] // 10
    ultra_r12 = r12_count // 10
    total_km = career['total_km']
    galaxy = total_km // 100000
    mondial = total_km // 40000
    long_routes = {
        str(r.get('route_name') or '').strip().lower()
        for r in rides if (r.get('distance_km') or 0) >= 1200
    }
    coast_to_coast = len(long_routes) // 4
    challenge_seasons = sum(
        1 for season in seasons_with_summaries(rides, today)
        if sum(1 for r in season['brevets'] if (r.get('distance_km') or 0) >= 1200) >= 2
    )
    rando_scout = len({
        str(r.get('route_name') or '').strip().lower()
        for r in rides if str(r.get('route_name') or '').strip()
    }) // 25
    pbp_count = len(pbp_ancien_years(rides))

    candidates = [
        ('ultra_khound', 'Ultra K-Hound', ultra_k_hounds, 120),
        ('ultra_randonneur', 'Ultra Randonneur', ultra_randonneurs, 115),
        ('ultra_r12', 'Ultra R-12', ultra_r12, 110),
        ('galaxy', 'Galaxy', galaxy, 105),
        ('coast_to_coast', 'Coast-to-Coast 1200k', coast_to_coast, 100),
        ('mondial', 'Mondial', mondial, 95),
        ('k_hound', 'K-Hound', k_hounds, 90),
        ('american_challenge', 'American Randonneur Challenge', challenge_seasons, 85),
        ('r12', 'R-12', r12_count, 80),
        ('sr', 'Super Randonneur', career['total_sr'], 75),
        ('rando_scout', 'Rando Scout', rando_scout, 70),
        ('pbp_ancien', 'PBP Ancien', pbp_count, 65),
        ('permanents', 'Permanents', career['permanent_count'], 40),
        ('populaires', 'Populaires', career['populaire_count'], 30),
    ]
    icons = {
        'ultra_khound': '🏆', 'ultra_randonneur': '🏆', 'ultra_r12': '🏆',
        'galaxy': '🌌', 'coast_to_coast': '🗺️', 'mondial': '🌍',
        'k_hound': '🐕', 'american_challenge': '🇺🇸', 'r12': '🔁',
        'sr': '⭐', 'rando_scout': '🧭', 'pbp_ancien': '🚴',
        'permanents': '📍', 'populaires': '🎖️',
    }
    return [
        {'key': key, 'name': name, 'count': int(count), 'priority': priority,
         'icon': icons.get(key, '🏅')}
        for key, name, count, priority in candidates if count
    ]
