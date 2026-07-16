"""shared/plan_match.py — club-agnostic ride↔plan name matching.

Extracted into the standalone ``shared/`` library (extract -> shim -> vendor, as
with shared/fitness, shared/pacing) so BOTH the Team Asha app and BrevetHub reuse
the SAME matcher. ``services/plan_match`` is now a full-module shim over this file,
and ``brevethub/shared/plan_match.py`` is a byte-identical vendored copy.

Used by the Team Asha web (routes/riders.py) and mobile API (routes/live.py) so a
ride with no ride_plan_id FK still resolves to its plan the SAME way on both
surfaces (avoids web/mobile drift). The functions are unchanged from the original
_normalize_route / _match_plans_to_events.
"""
import re

# Words too generic for single-word matching.
GENERIC_WORDS = {'200', '300', '302', '400', '600', '1000', '1200',
                 '200k', '300k', '400k', '600k', '1000k', '1200k',
                 'city', 'lake', 'valley', 'creek', 'mountain', 'mountains',
                 'coast', 'bay', 'point', 'beach', 'night', 'gold', 'river',
                 'davis', 'del', 'san'}


def normalize_route(name):
    """Normalize a route name for matching: lowercase, strip common suffixes."""
    s = (name or '').lower()
    s = re.sub(r'&nbsp;', ' ', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\b(plan|route|brevet|k|km|mi)\b', '', s)
    s = re.sub(r'\b(20\d{2})\b', '', s)  # remove years
    s = re.sub(r'#\d+', '', s)  # remove brevet numbers
    # Strip standalone short numbers (e.g. "Day 2", "Stage 3") — too generic and
    # they produce false positives like "200K Mostly SLO Day 2" → "Del Peurto 200K #2".
    s = re.sub(r'\b\d{1,2}\b', '', s)
    return set(s.split()) - {'', 'the', 'a', 'and', 'of', 'in', 'to', 'scr', 'sfr', 'dbc', 'sr', 'ta'}


def match_plan(name, plans):
    """Best-matching plan dict for a route name, or None.

    Same rule the web uses: require ≥1 distinctive (non-generic) common word AND
    ≥2 common words; score by overlap and pick the highest. `plans` is an iterable
    of dicts with at least 'name' (and usually 'slug').
    """
    e_words = normalize_route(name)
    best, best_score = None, 0
    for plan in plans:
        common = e_words & normalize_route(plan['name'])
        distinctive = common - GENERIC_WORDS
        if len(distinctive) >= 1 and len(common) >= 2:
            score = len(common) + len(distinctive)
            if score > best_score:
                best, best_score = plan, score
    return best
